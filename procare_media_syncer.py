import json
import mimetypes
import requests
import sys
import os
import piexif
from datetime import datetime
from dataclasses import dataclass
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from PIL import Image
from urllib.parse import urlparse

# Your credentials file from the Google Cloud Console
PHOTOS_CREDENTIALS_FILE = "/secrets/google_photos_credentials.json"
PHOTOS_TOKEN_FILE = "/secrets/token.json"
PHOTOS_ALBUM_IDS_FILE = "/secrets/photos_album_ids.txt"
PROCARE_CREDENTIALS_FILE = "/secrets/procare_credentials.json"

# Google Photos API scopes so that the app has permission for just photos
SCOPES = ["https://www.googleapis.com/auth/photoslibrary"]

PHOTOS_API_BASE_URL = "https://photoslibrary.googleapis.com/v1"
PROCARE_API_BASE_URL = "https://api-school.procareconnect.com/api/web"

PHOTOS_CREATE_ALBUM_ENDPOINT = f"{PHOTOS_API_BASE_URL}/albums"
PHOTOS_UPLOAD_ENDPOINT = f"{PHOTOS_API_BASE_URL}/uploads"
PHOTOS_ADD_MEDIA_ITEMS_ENDPOINT = (
    f"{PHOTOS_API_BASE_URL}/mediaItems:batchCreate"
)
PHOTOS_LIST_MEDIA_ITEMS_ENDPOINT = f"{PHOTOS_API_BASE_URL}/mediaItems:search"
PROCARE_LIST_ACTIVITIES_ENDPOINT = (
    f"{PROCARE_API_BASE_URL}/parent/daily_activities/"
)
PROCARE_LIST_KIDS_ENDPOINT = f"{PROCARE_API_BASE_URL}/parent/kids/"
PROCARE_AUTH_ENDPOINT = f"{PROCARE_API_BASE_URL}/auth"

DOWNLOADS_DIR = "/home/homeassistant/procare-syncer/downloads/"

@dataclass
class KidProfile:
  kid_id: str
  name: str
  album_id: str


def print_failure(prefix_str, response):
  """Prints a failure message given a prefix string & HTTP response

  Arguments:
    prefix_str: The prefix string for the error message
    response: The HTTP response to print for debugging
  """
  print(
      prefix_str + f" code: {response.status_code}, reason:\n{response.text}"
  )


def update_photo_exif_data(media_filename, activity_time):
  """Updates the EXIF metadata of the file with the given time

  Arguments:
    media_filename: The filename of to update
    activity_time: The timestamp to set as creation time
  """
  creation_time = datetime.fromisoformat(activity_time).strftime(
      "%Y:%m:%d %H:%M:%S"
  )

  image = Image.open(media_filename)
  exif_dict = piexif.load(image.info["exif"])
  exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = creation_time
  exif_dict['Exif'][piexif.ExifIFD.DateTimeDigitized] = creation_time
  exif_dict['0th'][piexif.ImageIFD.DateTime] = creation_time
  exif_bytes = piexif.dump(exif_dict)
  image.save(media_filename, "jpeg", exif=exif_bytes)


def authenticate_with_google_photos(base_dir):
  """Authenticates with Google Photos given a credentials / tokens file.
  
  The credentials JSON file (PHOTOS_CREDENTIALS_FILE) needs to be downloaded
  Google Cloud Console for the first time authentication. During first time
  authentication, a token file (PHOTOS_TOKEN_FILE) is generated which is used
  for subsequent authentication.

  Arguments:
    base_dir: Base directory to read files from 
  
  Returns:
    The Google Photos credentials
  """
  creds = None

  token_file = base_dir + PHOTOS_TOKEN_FILE
  if os.path.exists(token_file):
    creds = Credentials.from_authorized_user_file(token_file, SCOPES)

  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          base_dir + PHOTOS_CREDENTIALS_FILE, SCOPES
      )
      creds = flow.run_local_server(port=0)

    with open(token_file, "w") as token:
      token.write(creds.to_json())

  return creds

def create_album(creds, kid_name):
  """Creates an album given the kid's name

  Arguments:
    creds: The credentials to use for Google Photos
    kid_name: The kid's name

  Returns:
    The album id
  """
  headers = {
      "Authorization": f"Bearer {creds.token}",
      'Content-Type': 'application/json'
  }
  request_data = { "album" : { "title" : f"{kid_name} School Pictures!" } }
  response = requests.post(
      PHOTOS_CREATE_ALBUM_ENDPOINT, headers=headers, data=str(request_data)
  )
  if response.status_code != 200:
    print_error("Photos create album failed", response)

  print(f"Created new album: {response.json()}")
  return response.json().get("id")


def upload_photo_bytes(creds, filename):
  """Uploads the photo from filename and returns the upload token.

  Args:
    creds: The Google Photos creds to use for the upload

  Returns:
    The upload token
  """

  mime_type = str(mimetypes.guess_type(filename)[0])
  headers = {
      "Authorization": f"Bearer {creds.token}",
      "Content-type": "application/octet-stream",
      "X-Goog-Upload-Content-Type": mime_type,
      "X-Goog-Upload-Protocol": "raw",
  }
  try:
    # We need the binary data for the request body
    with open(DOWNLOADS_DIR + filename, "rb") as file:
      binary_data = file.read()

    response = requests.post(
        PHOTOS_UPLOAD_ENDPOINT, headers=headers, data=binary_data
    )

    if response.status_code == 200:
      print(f"Uploaded {filename} successfully.")
      return response.text  # Return the upload token
    else:
      print_failure("Upload failed", response)
  except Exception as e:
    print(e)


def add_media_to_album(creds, album_id, filename_desc_map):
  """Adds media to the album with the provided filenames and descriptions

  Uploads media from filename_desc_map to the specified Google Photos album_id.
    * Google Photos API supports setting a filename for each media item. We're
      setting that to our locally generated filename.
    * Google Photos API also supports setting a description for each media item.
      We set that to the value from filename_desc_map.

  Args:
    creds: The Google Photos credentials to use for the request
    album_id: The Google Photos album id to add media to
    filename_desc_map: A dictionary of filename to descriptions
  """
  filenames = list(filename_desc_map.keys())
  print(f"Adding {len(filenames)} files to add to album: {filenames}")

  if len(filenames) == 0:
    return

  batch_size = 50
  headers = {
      "Content-type": "application/json",
      "Authorization": f"Bearer {creds.token}",
  }

  # Add files to album in multiple batches with num files <= batch_size
  for i in range(0, len(filenames), batch_size):
    new_media_items = []
    for filename in filenames[i : i + batch_size]:
      upload_token = upload_photo_bytes(photos_creds, filename)
      new_media_items.append({
          "description": filename_desc_map.get(filename),
          "simpleMediaItem": {
              "fileName": filename,
              "uploadToken": upload_token,
          },
      })

    request_body = {
        "albumId": album_id,
        "newMediaItems": new_media_items,
    }

    response = requests.post(
        PHOTOS_ADD_MEDIA_ITEMS_ENDPOINT, json=request_body, headers=headers
    )
    if response.status_code == 200:
      print(
          f"Adding {len(new_media_items)} photos to the album was successful."
      )
    else:
      print_failure("POST request to add media items failed", response)


def authenticate_with_procare(base_dir, session):
  """Authenticates with Procare and updates the session with the credentials.

  Arguments:
    base_dir: Base directory to read files from
    session: The session object to be updated
  """
  with open(base_dir + PROCARE_CREDENTIALS_FILE) as file:
    procare_creds = json.load(file)
    file.close()

  response = session.post(PROCARE_AUTH_ENDPOINT, json=procare_creds)
  if response.status_code == 201:
    auth_token = response.json()["user"]["auth_token"]
    session.headers.update({"Authorization": "Bearer " + auth_token})
  else:
    print_failure("Authentication with Procare failed", response)


def get_file_ext(media_url, activity_type):
  """Get the file extension from the media url's basename

  Returns the file extension including the '.' if available. If not, it returns
  a default based on the activity_type (.jpg/.mp4)

  Arguments:
    media_url: The media url of the file being downloaded
    activity_type: The activity_type classification of the activity on Procare.

  Returns:
    The string file extension.
  """
  media_filename = os.path.basename(urlparse(media_url).path)
  filename, ext = os.path.splitext(media_filename)

  if ext is not None and ext != "":
    return ext
  elif activity_type == "photo_activity":
    return ".jpg"
  elif activity_type == "video_activity":
    return ".mp4"


def download_media(session, media_url, media_filename):
  """Downloads media from a given media_url into media_filename
  
  Arguments:
    session: The session object with Procare auth header
    media_url: The url from which media should be downloaded
    media_filename: The filename in DOWNLOADS_DIR to download the media into
  
  Returns:
    No return value.
  """
  response = session.get(media_url)
  if response.status_code != 200:
    print_failure("Media download failed", response)
    return

  print(f"Downloaded new media: {media_filename}")

  media_filepath = DOWNLOADS_DIR + media_filename
  with open(media_filepath, "wb") as file_handler:
    file_handler.write(response.content)
    file_handler.close()


def procare_download_new_media(session, kid_id, existing_filenames):
  """Downloads new media files (not present in existing_filenames) from Procare
  
  This method does the following:
    * Lists all activities from Procare until current date (page by page)
    * Ignores activities that do not have media (photo / video)
    * Download media files that are not already present in existing_filenames
 
  Arguments:
    existing_filenames: List of filenames already present in Photos album  
    kid_id: The kid to download media for  

  Returns:
    A dictionary of newly downloaded filenames --> corresponding captions.
  """

  page_num = 1
  filename_desc_map = {}
  current_date = datetime.today().strftime("%Y-%m-%d")
  while True:
    response = session.get(
        PROCARE_LIST_ACTIVITIES_ENDPOINT,
        params = {
            "kid_id": kid_id,
            "filters[daily_activity][date_to]": current_date,
            "page": str(page_num),
        },
    )
    if response.status_code != 200:
      print_failure("Procare list_activities failed", response)
      return

    activities = response.json()["daily_activities"]
    if len(activities) == 0:
      break

    print(f"Listing {len(activities)} activities from page: {page_num}")

    for activity in activities:
      media_url = None
      if activity["activity_type"] == "photo_activity":
        media_url = activity["activiable"]["main_url"]
      elif activity["activity_type"] == "video_activity":
        media_url = activity["activiable"]["video_file_url"]
      
      if media_url is None:
        continue

      media_filename = activity["activity_time"] + "_" + activity["id"]
      media_filename += get_file_ext(media_url, activity["activity_type"])
      if media_filename in existing_filenames:
        continue

      activity_time = activity.get("activity_time")
      download_media(session, media_url, media_filename)

      # TODO: Figure out a similar solution for videos.
      if activity["activity_type"] == "photo_activity":
        update_photo_exif_data(DOWNLOADS_DIR + media_filename, activity_time)

      description = activity.get("activiable").get("caption")
      filename_desc_map[media_filename] = description

    page_num += 1

  return filename_desc_map


def list_media_in_album(creds, album_id):
  """List filenames of media in the provided Google Photos album_id

  Arguments:
    creds: The Google Photos credentials to use
    album_id: The Google Photos album id to list media from

  Returns:
    List of filenames in the album
  """
  headers = { "Authorization": f"Bearer {creds.token}" }
  params = { "albumId": album_id, "pageSize": 50 }

  existing_filenames = []
  while True:
    response = requests.post(
        PHOTOS_LIST_MEDIA_ITEMS_ENDPOINT, headers=headers, json=params
    )

    if response.status_code == 200:
      media_items = response.json().get("mediaItems", [])

      for media_item in media_items:
        existing_filenames.append(media_item.get("filename"))

      # nextPageToken will be present only if there are more pages
      next_page_token = response.json().get("nextPageToken")
      if next_page_token:
        params["pageToken"] = next_page_token
      else:
        break
    else:
      print_failure("Failed to list photos in album", response)
      break

  return existing_filenames


def create_kid_profiles(base_dir, photos_creds, session):
  """Create a profile for each kid in the Procare account

  Arguments:
    base_dir: Base directory to read files from
    photos_creds: The Google Photos creds to create an album if necessary
    session: The Procare session object to use for Procare requests

  Returns:
    A list of KidProfiles
  """
  response = session.get(PROCARE_LIST_KIDS_ENDPOINT)

  if response.status_code != 200:
    print_failure("Procare list kids failed", response)

  album_ids = []
  # Open with mode 'w+' so that the file is created if it doesn't exist.
  with open(base_dir + PHOTOS_ALBUM_IDS_FILE, 'r+') as f:
    album_ids = f.read().splitlines()

  index = 0
  kid_profiles = []
  
  for entry in response.json().get("kids"):
    kid_name = entry.get("first_name")

    if index + 1 > len(album_ids):
      album_ids.append(create_album(photos_creds, kid_name))
    
    kid_profile = KidProfile(entry.get("id"), kid_name, album_ids[index])
    kid_profiles.append(kid_profile)
    index += 1

  # Write back to the file in case new albums were created above
  with open(base_dir + PHOTOS_ALBUM_IDS_FILE, 'w') as f:
    for album_id in album_ids:
      f.write(f"{album_id}\n")
  
  return kid_profiles

if __name__ == "__main__":
  base_dir = os.path.dirname(os.path.abspath(__file__))
  print(f"Executing with base_dir: {base_dir}")

  photos_creds = authenticate_with_google_photos(base_dir)

  session = requests.Session()
  authenticate_with_procare(base_dir, session)

  kid_profiles = create_kid_profiles(base_dir, photos_creds, session)
  print(f"Found {len(kid_profiles)} kid profiles")
  
  for kid_profile in kid_profiles:
    print(f"Processing kid: {kid_profile.name}")

    existing_filenames = list_media_in_album(photos_creds, kid_profile.album_id)
    print(f"Found {len(existing_filenames)} in the Google Photos album")
    
    filename_desc_map = procare_download_new_media(
        session, kid_profile.kid_id, existing_filenames)
    print(f'Downloaded {len(filename_desc_map)} new files from Procare')

    add_media_to_album(photos_creds, kid_profile.album_id, filename_desc_map)
