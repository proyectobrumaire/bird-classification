"""
drive_download_process_upload.py

Pipeline stages:
1. Connect to Google Drive
2. Download all images from ONE Drive folder into a temporary directory
3. Process images (placeholder: generate CSV)
4. Upload CSV file back to Google Drive
5. Optionally delete temporary files (controlled by flag)

No AI yet — processing stage is a placeholder.
"""

import os
import tempfile
from typing import List, Dict

# If the following lines are not recognized, please run:
# pip install google-api-python-client google-auth google-auth-oauthlib

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request


# ----------------------------
# CONFIG
# ----------------------------
SCOPES = ["https://www.googleapis.com/auth/drive"]
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"

# https://drive.google.com/drive/folders/1k_WUeTwcc86hLbAewPMxWBfoRgIxnYfa
# DRIVE_FOLDER_ID = "1WJqbQrd2VAH08CtiM6GDhmb3DtMLRI9e"
DRIVE_FOLDER_ID = "1k_WUeTwcc86hLbAewPMxWBfoRgIxnYfa"

DELETE_TEMP_FILES = True  # <-- CONTROL CLEANUP HERE


# ----------------------------
# AUTHORIZATION
# ----------------------------
def get_drive_service():
    """
    Authenticate and return a Google Drive API service instance.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


# ----------------------------
# DRIVE OPERATIONS – DOWNLOAD
# ----------------------------
def list_images(service, folder_id: str) -> List[Dict]:
    """
    List all image files in a Google Drive folder.
    """
    query = (
        f"'{folder_id}' in parents "
        "and trashed = false "
        "and mimeType contains 'image/'"
    )

    files = []
    page_token = None

    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute()

        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return files


def download_image(service, file_id: str, output_path: str):
    """
    Download a single Drive file to disk.
    """
    request = service.files().get_media(fileId=file_id)
    with open(output_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def download_images_from_drive(service, folder_id: str) -> str:
    """
    Download all images from Drive into a temporary directory.

    Returns:
        Path to temporary directory containing images.
    """
    images = list_images(service, folder_id)
    print(f"Found {len(images)} images")

    temp_dir = tempfile.mkdtemp(prefix="drive_images_")
    print("Temporary directory created:", temp_dir)

    for img in images:
        out_path = os.path.join(temp_dir, img["name"])
        download_image(service, img["id"], out_path)
        print(f"Downloaded: {img['name']}")

    return temp_dir


# ----------------------------
# PROCESSING STAGE (PLACEHOLDER)
# ----------------------------

"""
Placeholder processing stage.

Later:
    - Run AI on images
    - Fill CSV with predictions

For now:
    - Write dummy content
"""


from dl_main import main  # your detector file
from pathlib import Path
from tqdm import tqdm

def process_images_and_create_csv(images_dir: str):
    images_dir = Path(images_dir)

    valid_exts = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}

    image_files = [
        p for p in images_dir.iterdir()
        if p.suffix.lower() in valid_exts
    ]

    for img_path in tqdm(image_files, desc="Processing images"):
        # --- run your pipeline ---
        annotated, classified_results = main(
            mask_or_bbox="bbox",
            img_path=str(img_path)
        )

        # --- save image ---
        out_img = images_dir / f"{img_path.stem}_pred.png"
        annotated.save(out_img)

        # --- save csv ---
        out_csv = images_dir / f"{img_path.stem}_pred.csv"
        with open(out_csv, "w", encoding="utf-8") as f:
            f.write("detector_score,species,confidence,keep,x1,y1,x2,y2\n")
            for r in classified_results:
                x1, y1, x2, y2 = r["padded_box"]
                f.write(
                    f"{r['detector_score']},"
                    f"{r['pred_species']},"
                    f"{r['pred_conf']},"
                    f"{r['keep']},"
                    f"{x1},{y1},{x2},{y2}\n"
                )
    # input("Press ENTER to delete temporary files...")
    print("\n[OK] All images processed.")

# ----------------------------
# DRIVE OPERATIONS – UPLOAD
# ----------------------------

import mimetypes

def upload_file_to_drive(service, file_path: str, folder_id: str):
    mime_type, _ = mimetypes.guess_type(file_path)

    media = MediaFileUpload(
        file_path,
        mimetype=mime_type,
        resumable=True
    )

    file_metadata = {
        "name": os.path.basename(file_path),
        "parents": [folder_id]
    }

    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name"
    ).execute()

    print(f"Uploaded: {uploaded['name']}")


# ----------------------------
# CLEANUP
# ----------------------------
def cleanup_temp_directory(temp_dir: str):
    """
    Delete temporary directory and its contents.
    """
    print("Deleting temporary directory:", temp_dir)

    for root, dirs, files in os.walk(temp_dir, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            os.rmdir(os.path.join(root, name))

    os.rmdir(temp_dir)


# ----------------------------
# MAIN PIPELINE
# ----------------------------
if __name__ == "__main__":

    service = get_drive_service()

    # 1. Download
    temp_images_dir = download_images_from_drive(
        service,
        DRIVE_FOLDER_ID
    )

    # 2. Process
    csv_output_path = os.path.join(
        os.path.join(temp_images_dir),
        "results_dummy.csv"
    )

    process_images_and_create_csv(
        images_dir=temp_images_dir
    )

    # # 3. Upload
    # upload_csv_to_drive(
    #     service,
    #     csv_output_path,
    #     DRIVE_FOLDER_ID
    # )
    # 3. Upload all CSVs
    for file in os.listdir(temp_images_dir):
        if file.endswith("_pred.csv") or file.endswith("_pred.png"):
            full_path = os.path.join(temp_images_dir, file)
            upload_file_to_drive(service, full_path, DRIVE_FOLDER_ID)


    # 4. Cleanup (controlled)
    if DELETE_TEMP_FILES:
        cleanup_temp_directory(temp_images_dir)
        print("Temporary files deleted.")
    else:
        print("Temporary files preserved at:", temp_images_dir)
