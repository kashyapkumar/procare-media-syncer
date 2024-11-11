import json
import os
import requests
import piexif
import shutil
import mimetypes
import pathlib
import subprocess
from datetime import datetime
from urllib.parse import urlparse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from PIL import Image

# Your credentials file from the Google Cloud Console
PHOTOS_CREDENTIALS_FILE = 'secrets/google_photos_credentials.json' 
PHOTOS_TOKEN_FILE = 'secrets/token.json'
PROCARE_CREDENTIALS_FILE = 'secrets/procare_credentials.json'

# Google Photos API scopes so that the app has permission for just photos
SCOPES = ["https://www.googleapis.com/auth/photoslibrary"]

PHOTOS_API_BASE_URL = 'https://photoslibrary.googleapis.com/v1'
PROCARE_API_BASE_URL = 'https://api-school.procareconnect.com/api/web'

PHOTOS_LIST_ALBUMS_ENDPOINT = 'https://photoslibrary.googleapis.com/v1/albums'
PHOTOS_UPLOAD_ENDPOINT = f'{PHOTOS_API_BASE_URL}/uploads'
PHOTOS_ADD_MEDIA_ITEMS_ENDPOINT = f'{PHOTOS_API_BASE_URL}/mediaItems:batchCreate'
PROCARE_LIST_ACTIVITIES_ENDPOINT = f'{PROCARE_API_BASE_URL}/parent/daily_activities/'
PROCARE_AUTH_ENDPOINT = f'{PROCARE_API_BASE_URL}/auth'

GOOGLE_PHOTOS_ALBUM_ID = 'AG1aZA-O5Vqaepq4ot53MSqAjUfJROPKiITAqRtAdQALnONFKx3cR_8WS6SMmv6Vqwm0ZHz5WYnJ'

def create_album(creds):
    headers = {
        "Authorization": f"Bearer {creds.token}",
        'Content-Type': 'application/json'
    }

    request_data = {"album" : {"title" : "Ishan Stratford Pics!!" }}
    response = requests.post(PHOTOS_LIST_ALBUMS_ENDPOINT, headers=headers, data=str(request_data))
    print(response.text)

def list_albums(creds):
    headers = {
        "Authorization": f"Bearer {creds.token}",
    }
    
    next_page_token = None
    while True:
        response = requests.get(PHOTOS_LIST_ALBUMS_ENDPOINT,
                headers=headers, 
                params={
                    'pageSize': 50,
                    'pageToken': next_page_token
                    })
        print(response.text)
        next_page_token = response.json()["nextPageToken"]

def update_photo_exif_data(image_filename, activity_time):
    image = Image.open(image_filename)
    exif_dict = piexif.load(image.info["exif"])
    exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = activity_time
    exif_dict['Exif'][piexif.ExifIFD.DateTimeDigitized] = activity_time
    exif_dict['0th'][piexif.ImageIFD.DateTime] = activity_time
    exif_bytes = piexif.dump(exif_dict)
    image.save(image_filename, "jpeg", exif=exif_bytes)

    activity_datetime = datetime.fromisoformat(activity_time)
    os.utime(image_filename, (activity_datetime.timestamp(), activity_datetime.timestamp()))

def print_failure(prefix_str, response):
    print(prefix_str + f" with status code {response.status_code}, reason:\n{repsonse.text}")

def authenticate_with_google_photos():
    creds = None

    if os.path.exists(PHOTOS_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(PHOTOS_TOKEN_FILE, SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(PHOTOS_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(PHOTOS_TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return creds

def get_mime(file):
    mime = mimetypes.guess_type(file)
    return(str(mime[0]))

def upload_photo_bytes(creds, filename):
    mime_type = get_mime(filename)
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-type": "application/octet-stream",
        "X-Goog-Upload-Content-Type": mime_type,
        "X-Goog-Upload-Protocol": "raw"
    }
    try:
        # We need the binary data for the request body
        with open(filename, "rb") as file:
            binary_data = file.read()

        response = requests.post(PHOTOS_UPLOAD_ENDPOINT, headers=headers, data=binary_data)

        if response.status_code == 200:
            print(f"Uploaded {filename} successfully.")
            return response.text # upload token
        else:
            print_failure("Upload failed", response)
    except Exception as e:
        print(e)

def add_photos_to_album(creds, filename_token_map):
    print(f"Number of tokens to add to album: {len(filename_token_map)}")

    if len(filename_token_map) == 0:
        return

    batch_size = 50

    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {creds.token}"
    }

    # Convert the dictionary values (upload tokens) into a list
    filenames = list(filename_token_map.keys())

    # Split the list of tokens into batches of 45 or less
    for i in range(0, len(filenames), batch_size):
        # Create newMediaItems for this batch
        new_media_items = []
        for filename in filenames[i:i + batch_size]:
            # Get the filename associated with the upload token
            new_media_items.append({
                "description": "Photos from the alpha.",
                "simpleMediaItem": {
                    "fileName": filename,
                    "uploadToken": filename_token_map[filename]
                }
            })

        request_body = {
            "albumId": GOOGLE_PHOTOS_ALBUM_ID,
            "newMediaItems": new_media_items
        }

        response = requests.post(PHOTOS_ADD_MEDIA_ITEMS_ENDPOINT, json=request_body, headers=headers)
        if response.status_code == 200:
            print(f"Adding {len(new_media_items)} photos to the album was successful.")
        else:
            print(f"POST request failed with status code: {response.status_code}")
            print(response.text)

def authenticate_with_procare(session):
    with open(PROCARE_CREDENTIALS_FILE) as file:
        procare_creds = json.load(file)
        file.close()
    
    response = session.post(PROCARE_AUTH_ENDPOINT, json=procare_creds)
    if response.status_code == 201:
        auth_token = response.json()['user']['auth_token']
        return auth_token
    else:
        print(f"Authentication with ProCare failed with status code {response.status_code}, reason:\n{response.text}")

def get_file_ext_from_url(photo_url):
    image_filename = os.path.basename(urlparse(photo_url).path)
    filename, ext = os.path.splitext(image_filename)
    return ext

def download_from_procare(photos_creds, filename_token_map):
    session = requests.Session()
    auth_token = authenticate_with_procare(session)
    session.headers.update({'Authorization': 'Bearer ' + auth_token})

    current_date = datetime.today().strftime('%Y-%m-%d')
    response = session.get(PROCARE_LIST_ACTIVITIES_ENDPOINT, params={
        'kid_id': '1878ff2c-30f0-4a14-8b4b-6ff42d12c701',
        'filters[daily_activity][date_to]': current_date,
        'page': '1'})
    if response.status_code != 200:
        print_failure("Procare list_activities failed", response)
        return

    activities = response.json()["daily_activities"]
    for activity in activities:
        photo_url = activity["photo_url"]
        if photo_url is None:
            continue
        
        print(f'Fetching picture from url: {photo_url}')
        response = session.get(photo_url)
        if response.status_code != 200:
            print_failure("Media download failed", response)

        activity_time = activity["activity_time"]
        image_filename = activity_time + "_" + activity["id"]
        image_filename += get_file_ext_from_url(photo_url)
        with open(image_filename, "wb") as file_handler:
            file_handler.write(response.content)
            file_handler.close()
            print(f"Successfully wrote file: {image_filename}")

        print(f'Setting image time to: {activity_time}')

        subprocess.run(["touch", f"-d {activity_time}", f"{image_filename}"])
        filename_token_map[image_filename] = upload_photo_bytes(photos_creds, image_filename)
        return

if __name__ == "__main__":
    photos_creds = authenticate_with_google_photos()
    filename_token_map = {}
    download_from_procare(photos_creds, filename_token_map)
    add_photos_to_album(photos_creds, filename_token_map)
