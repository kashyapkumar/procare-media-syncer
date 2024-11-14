from datetime import datetime
import json
import mimetypes
import os
import subprocess
from urllib.parse import urlparse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import requests

# Your credentials file from the Google Cloud Console
PHOTOS_CREDENTIALS_FILE = (
    "/home/homeassistant/procare-syncer/secrets/google_photos_credentials.json"
)
PHOTOS_TOKEN_FILE = "/home/homeassistant/procare-syncer/secrets/token.json"

# JSON file with the email and password for Procare
PROCARE_CREDENTIALS_FILE = (
    "/home/homeassistant/procare-syncer/secrets/procare_credentials.json"
)

# Google Photos API scopes so that the app has permission for just photos
SCOPES = ["https://www.googleapis.com/auth/photoslibrary"]

PHOTOS_API_BASE_URL = "https://photoslibrary.googleapis.com/v1"
PROCARE_API_BASE_URL = "https://api-school.procareconnect.com/api/web"

PHOTOS_UPLOAD_ENDPOINT = f"{PHOTOS_API_BASE_URL}/uploads"
PHOTOS_ADD_MEDIA_ITEMS_ENDPOINT = (
    f"{PHOTOS_API_BASE_URL}/mediaItems:batchCreate"
)
PHOTOS_LIST_MEDIA_ITEMS_ENDPOINT = f"{PHOTOS_API_BASE_URL}/mediaItems:search"
PROCARE_LIST_ACTIVITIES_ENDPOINT = (
    f"{PROCARE_API_BASE_URL}/parent/daily_activities/"
)
PROCARE_AUTH_ENDPOINT = f"{PROCARE_API_BASE_URL}/auth"

DOWNLOADS_DIR = "/home/homeassistant/procare-syncer/downloads/"

PHOTOS_ALBUM_ID = "AG1aZA-O5Vqaepq4ot53MSqAjUfJROPKiITAqRtAdQALnONFKx3cR_8WS6SMmv6Vqwm0ZHz5WYnJ"

def print_failure(prefix_str, response):
  """Prints a failure message given a prefix string & HTTP response

  Args:
    prefix_str: The prefix string for the error message
    response: The HTTP response to print for debugging
  """
  print(
      prefix_str + f" code: {response.status_code}, reason:\n{response.text}"
  )


def authenticate_with_google_photos():
  """Authenticates with Google Photos given a credentials / tokens file.
  
  The credentials JSON file (PHOTOS_CREDENTIALS_FILE) needs to be downloaded
  Google Cloud Console for the first time authentication. During first time
  authentication, a token file (PHOTOS_TOKEN_FILE) is generated which is used
  for subsequent authentication.
  
  Returns:
    The Google Photos credentials
  """
  creds = None

  if os.path.exists(PHOTOS_TOKEN_FILE):
    creds = Credentials.from_authorized_user_file(PHOTOS_TOKEN_FILE, SCOPES)

  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          PHOTOS_CREDENTIALS_FILE, SCOPES
      )
      creds = flow.run_local_server(port=0)

    with open(PHOTOS_TOKEN_FILE, "w") as token:
      token.write(creds.to_json())

  return creds


def get_mime(filename):
  """Returns the mime type of the file given the filename"""
  mime = mimetypes.guess_type(filename)
  return str(mime[0])


def upload_photo_bytes(creds, filename):
  """Uploads the photo from filename and returns the upload token.

  Args:
    creds: The Google Photos creds to use for the upload

  Returns:
    The upload token
  """
  headers = {
      "Authorization": f"Bearer {creds.token}",
      "Content-type": "application/octet-stream",
      "X-Goog-Upload-Content-Type": get_mime(filename),
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


def add_photos_to_album(creds, filename_desc_map):
  """Adds photos to the album with the provided filenames and descriptions

  Uploads photos from filename_desc_map to PHOTOS_ALBUM_ID.
    * Google Photos API supports setting a filename for each media item. We're
      setting that to our locally generated filename.
    * Google Photos API also supports setting a description for each media item.
      We set that to the value from filename_desc_map.

  Args:
    creds: The Google Photos credentials to use for the request
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
              "fileName": DOWNLOADS_DIR + filename,
              "uploadToken": upload_token,
          },
      })

    request_body = {
        "albumId": PHOTOS_ALBUM_ID,
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


def authenticate_with_procare(session):
  with open(PROCARE_CREDENTIALS_FILE) as file:
    procare_creds = json.load(file)
    file.close()

  response = session.post(PROCARE_AUTH_ENDPOINT, json=procare_creds)
  if response.status_code == 201:
    auth_token = response.json()["user"]["auth_token"]
    return auth_token
  else:
    print_failure("Authentication with Procare failed", response)


def get_file_ext(media_url, activity_type):
  media_filename = os.path.basename(urlparse(media_url).path)
  filename, ext = os.path.splitext(media_filename)

  if ext is not None and ext != "":
    return ext
  elif activity_type == "photo_activity":
    return ".jpg"
  elif activity_type == "video_activity":
    return ".mp4"


def get_media_url_from_activity(activity):
  if activity["activity_type"] == "photo_activity":
    return activity["activiable"]["main_url"]
  elif activity["activity_type"] == "video_activity":
    return activity["activiable"]["video_file_url"]


def get_filename_from_activity(activity, media_url):
  media_filename = activity["activity_time"] + "_" + activity["id"]
  media_filename += get_file_ext(media_url, activity["activity_type"])
  return media_filename


def download_media(session, media_url, media_filename, activity_time):
  response = session.get(media_url)
  if response.status_code != 200:
    print_failure("Media download failed", response)
    return

  print(f"Downloaded new media: {media_filename}")

  media_filepath = DOWNLOADS_DIR + media_filename
  with open(media_filepath, "wb") as file_handler:
    file_handler.write(response.content)
    file_handler.close()

  subprocess.run(["touch", f"-d {activity_time}", f"{media_filepath}"])

  return media_filename


def download_from_procare(existing_filenames):
  session = requests.Session()
  auth_token = authenticate_with_procare(session)
  session.headers.update({"Authorization": "Bearer " + auth_token})

  page_num = 1
  filename_desc_map = {}
  current_date = datetime.today().strftime("%Y-%m-%d")
  while True:
    response = session.get(
        PROCARE_LIST_ACTIVITIES_ENDPOINT,
        params={
            "kid_id": "1878ff2c-30f0-4a14-8b4b-6ff42d12c701",
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
      media_url = get_media_url_from_activity(activity)
      if media_url is None:
        continue

      media_filename = get_filename_from_activity(activity, media_url)
      if media_filename in existing_filenames:
        continue

      activity_time = activity.get("activity_time")
      download_media(session, media_url, media_filename, activity_time)

      description = activity.get("activiable").get("caption")
      filename_desc_map[media_filename] = description

    page_num += 1

  return filename_desc_map


def list_photos_in_album(creds):
  headers = {
      "Authorization": f"Bearer {creds.token}",
  }
  params = {
      "albumId": PHOTOS_ALBUM_ID,
      "pageSize": 50,
  }

  existing_filenames = []

  while True:
    response = requests.post(
        PHOTOS_LIST_MEDIA_ITEMS_ENDPOINT, headers=headers, json=params
    )

    if response.status_code == 200:
      data = response.json()
      media_items = data.get("mediaItems", [])

      for media_item in media_items:
        existing_filenames.append(media_item.get("filename"))

      # nextPageToken will be present only if there are more pages
      next_page_token = data.get("nextPageToken")
      if next_page_token:
        params["pageToken"] = next_page_token
      else:
        break
    else:
      print_failure("Failed to list photos in album", response)
      break

  return existing_filenames


if __name__ == "__main__":
  photos_creds = authenticate_with_google_photos()
  existing_filenames = list_photos_in_album(photos_creds)
  print(f"Found {len(existing_filenames)} in the Google Photos album")

  filename_desc_map = download_from_procare(existing_filenames)
  print(f'Downloaded {len(filename_desc_map)} new files from Procare')

  add_photos_to_album(photos_creds, filename_desc_map)
