# Procare to Google Photos Media Syncer

If you're looking to automate adding the pictures/videos from your kid's Procare account to a Google Photos album, you've come to the right place!

## What does this do

* In the first run, for each kid on your Procare account, it creates a new Google Photos album with the kid's first name (as specified on Procare)
  * Note that due to Google Photos API's limitations, the script cannot add to a pre-existing album
* With every run, it looks for any new pictures/videos on the Procare account that aren't already present in the Google Photos album and adds them to the corresponding kid's album
* If the picture has a caption on the Procare app, it will set that caption as the description on Google Photos

## How to Use

Follow these simple steps:

  1. Clone this repository to your local machine: `git clone https://github.com/kashyapkumar/procare-media-syncer.git`
  2. Create a `secrets` directory under this repo for your secret credential files
  3. Generate a credentials json file for your Google Photos account
     1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a new project
     2. Enable the [Google Photos Library API](https://console.cloud.google.com/marketplace/product/google/photoslibrary.googleapis.com) for your project
     3. Create credentials for the Photos API
         1. Select OAuth Client ID and Web Application type
         2. Add `https://localhost/` as an authorized redirect URI
         3. Download the JSON file and save it as `google_photos_credentials.json` in your `secrets` directory
     4. Add yourself as a test user [for your Cloud Console project](https://console.cloud.google.com/auth/audience)
  4. Create a `procare_credentials.json` file in your `secrets` directory with the following JSON content:
      ```
      { "email": <procare email id>, "password": <plaintext password> }
      ```
  5. The first run of your script will trigger the browser to allow the script to access your Google account, follow the steps to approve
  6. Create a cronjob to do this periodically! I have it running every 5 mins from 7am-7pm M-F
      ```
      */5 7-19 * * 1-5 /usr/local/bin/python3 <path to file>/procare_downloader.py > /tmp/procare-log.txt 2>&1
      ```
  7. You're all set :)

## Known Issues / Future Work
* If two media files are byte equivalent, Photos API reports both as successfully uploaded, but only the first file is added to Google Photos.
  So if you have duplicate media files in your account, the script will try to re-upload the duplicates on every run.
* Multiple schools are not supported yet

Feel free to reach out to me / contribute directly if you see any issues :)

## Contact
* Email: kashyapcbe [at] gmail [dot] com
* LinkedIn: https://www.linkedin.com/in/kashyapkrishnakumar/

## References
* https://github.com/JWally/procare-media-downloader
* https://blog.nevinpjohn.in/posts/upload-to-google-photos-using-python/
