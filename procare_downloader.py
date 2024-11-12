import json
import os
import requests
import mimetypes
import subprocess
from datetime import datetime
from urllib.parse import urlparse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Your credentials file from the Google Cloud Console
PHOTOS_CREDENTIALS_FILE = 'secrets/google_photos_credentials.json' 
PHOTOS_TOKEN_FILE = 'secrets/token.json'

# JSON file with the email and password for Procare
PROCARE_CREDENTIALS_FILE = 'secrets/procare_credentials.json'

# Google Photos API scopes so that the app has permission for just photos
SCOPES = ["https://www.googleapis.com/auth/photoslibrary"]

PHOTOS_API_BASE_URL = 'https://photoslibrary.googleapis.com/v1'
PROCARE_API_BASE_URL = 'https://api-school.procareconnect.com/api/web'

PHOTOS_UPLOAD_ENDPOINT = f'{PHOTOS_API_BASE_URL}/uploads'
PHOTOS_ADD_MEDIA_ITEMS_ENDPOINT = f'{PHOTOS_API_BASE_URL}/mediaItems:batchCreate'
PHOTOS_LIST_MEDIA_ITEMS_ENDPOINT = f'{PHOTOS_API_BASE_URL}/mediaItems:search'
PROCARE_LIST_ACTIVITIES_ENDPOINT = f'{PROCARE_API_BASE_URL}/parent/daily_activities/'
PROCARE_AUTH_ENDPOINT = f'{PROCARE_API_BASE_URL}/auth'

DOWNLOADS_DIR = 'downloads/'

PHOTOS_ALBUM_ID = 'AG1aZA-O5Vqaepq4ot53MSqAjUfJROPKiITAqRtAdQALnONFKx3cR_8WS6SMmv6Vqwm0ZHz5WYnJ'

def print_failure(prefix_str, response):
    print(prefix_str + f" with status code {response.status_code}, reason:\n{response.text}")

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
        with open(DOWNLOADS_DIR + filename, "rb") as file:
            binary_data = file.read()

        response = requests.post(PHOTOS_UPLOAD_ENDPOINT, headers=headers, data=binary_data)

        if response.status_code == 200:
            print(f"Uploaded {filename} successfully.")
            return response.text # upload token
        else:
            print_failure("Upload failed", response)
    except Exception as e:
        print(e)

def add_photos_to_album(creds, filenames):
    print(f"Adding {len(filenames)} files to add to album: {filenames}")

    if len(filenames) == 0:
        return

    batch_size = 50
    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {creds.token}"
    }

    # Add files to album in multiple batches with num files <= batch_size
    for i in range(0, len(filenames), batch_size):
        new_media_items = []
        for filename in filenames[i:i + batch_size]:
            upload_token = upload_photo_bytes(photos_creds, filename)
            new_media_items.append({
                "description": "Photos from the alpha.",
                "simpleMediaItem": {
                    "fileName": DOWNLOADS_DIR + filename,
                    "uploadToken": upload_token
                }
            })

        request_body = {
            "albumId": PHOTOS_ALBUM_ID,
            "newMediaItems": new_media_items
        }

        response = requests.post(PHOTOS_ADD_MEDIA_ITEMS_ENDPOINT, json=request_body, headers=headers)
        if response.status_code == 200:
            print(f"Adding {len(new_media_items)} photos to the album was successful.")
        else:
            print_failure("POST request to add media items failed", response)

def authenticate_with_procare(session):
    with open(PROCARE_CREDENTIALS_FILE) as file:
        procare_creds = json.load(file)
        file.close()
    
    response = session.post(PROCARE_AUTH_ENDPOINT, json=procare_creds)
    if response.status_code == 201:
        auth_token = response.json()['user']['auth_token']
        return auth_token
    else:
        print_failure("Authentication with Procare failed", repsonse)

def get_file_ext(media_url, activity_type):
    media_filename = os.path.basename(urlparse(media_url).path)
    filename, ext = os.path.splitext(media_filename)

    if ext is not None and not ext == "":
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

def download_media_from_activity(session, activity, media_url):
    media_filename = get_filename_from_activity(activity, media_url)
    response = session.get(media_url)
    if response.status_code != 200:
        print_failure("Media download failed", response)
        return

    with open(DOWNLOADS_DIR + media_filename, "wb") as file_handler:
        file_handler.write(response.content)
        file_handler.close()

    activity_time = activity["activity_time"]
    subprocess.run(["touch", f"-d {activity_time}", f"{media_filename}"])
    return media_filename

def download_from_procare(existing_filenames):
    session = requests.Session()
    auth_token = authenticate_with_procare(session)
    session.headers.update({'Authorization': 'Bearer ' + auth_token})

    current_date = datetime.today().strftime('%Y-%m-%d')

    page_num = 1
    filenames = []
    while True:
        response = session.get(PROCARE_LIST_ACTIVITIES_ENDPOINT, params={
            'kid_id': '1878ff2c-30f0-4a14-8b4b-6ff42d12c701',
            'filters[daily_activity][date_to]': current_date,
            'page': str(page_num)})
        if response.status_code != 200:
            print_failure("Procare list_activities failed", response)
            return

        activities = response.json()["daily_activities"]
        if (len(activities) == 0):
            break

        for activity in activities:
            media_url = get_media_url_from_activity(activity)
            if media_url is None:
                continue

            if get_filename_from_activity(activity, media_url) in existing_filenames:
                continue

            media_filename = download_media_from_activity(session, activity, media_url)
            filenames.append(media_filename)

        page_num += 1
    
    return filenames

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
        response = requests.post(PHOTOS_LIST_MEDIA_ITEMS_ENDPOINT, headers=headers, json=params)

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
    filenames = download_from_procare(existing_filenames)
    add_photos_to_album(photos_creds, filenames)
