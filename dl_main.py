import torch
from PIL import Image
import matplotlib.pyplot as plt
import os
import pandas as pd
from pathlib import Path

# --- Detector imports: bird localization in the full image ---
from bird_detector import *

# --- Crop preparation: convert detections into classifier-ready crops ---
from utils_bounding_boxes_separation import prepare_crops_for_classifier

# --- Classifier imports: species prediction and visualization ---
from predict_image_from_tensors import (load_species_model, classify_crops_batch, annotate_full_image)

def main(mask_or_bbox, img_path):
    """
    Deep Learning prediction pipeline.

    Steps:
    1. Ask the user to choose an image file
    2. Run the bird detector to get bounding boxes
    3. Convert each detection into a cropped bird image
    4. Run the species classifier on each crop
    5. Draw bounding boxes and species labels on the original image
    6. Show the annotated image
    """

    # 1. Select and load image
    # img_path = pick_file_dialog()    # opens a GUI dialog and returns the chosen path
    pil_img = Image.open(img_path)
    # pil_img = Image.open("G:/Usuarios/Daniel/Descargas/bichofue.jpg").convert("RGB")

    # Decide whether to use GPU or CPU
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[INFO] Using device: {device}")

    # 2. Load detector and preprocess image for the detector
    if mask_or_bbox == "mask":
        detector_model, coco_classes = load_maskrcnn_detector(device)
        det_tensor = preprocess_image_for_detector(pil_img, device)
        # Run detector on the image
        detections = run_maskrcnn_on_image(detector_model, coco_classes,
                det_tensor,
                target_class="bird",
                conf_threshold=0.8 # minimum confidence threshold for keeping a detection
                )
        # masked_crop = apply_mask_to_crop(pil_img, detections[0]["mask"], detections[0]["box"], style="black")
        masked_crops = []  # list of (crop_pil, idx)

        for idx, det in enumerate(detections):
            box = det["box"]
            mask_hw = det["mask"]

            masked_crop = apply_mask_to_crop(
                pil_img,
                mask_hw,
                box,
                style="black",   # or "transparent"
            )

            masked_crops.append((masked_crop, idx))
            masked_crop.show()  # optional: see each one

    elif mask_or_bbox == "bbox":    
        detector_model, coco_classes = load_detector(device)
        det_tensor = preprocess_image_for_detector(pil_img, device)

        # Run detector on the image
        detections = run_detector_on_image(detector_model, coco_classes,
            det_tensor,
            target_class="bird",
            conf_threshold=0.8   # minimum confidence threshold for keeping a detection
            )


    print("\n[DEBUG] Detections from run_detector_on_image:")
    for i, d in enumerate(detections):
        print(f"  det #{i}: label={d['label']} score={d['score']:.3f} box={d['box']}")
    print(f"[DEBUG] Total detections: {len(detections)}")

    # 3. Convert detections into crops for the classifier
    crop_batches = prepare_crops_for_classifier(
        pil_img=pil_img,
        detections=detections,
        padding_ratio=0.10,
        min_conf=0.0,   # keep all detections
    )

    print("\n[DEBUG] Crops prepared for classifier:")
    for i, c in enumerate(crop_batches):
        print(f"  crop #{i}: det_label={c['label']} det_score={c['score']:.3f} box={c['padded_box']}")
    print(f"[DEBUG] Total crops: {len(crop_batches)}")

    # 4. Load bird species classifier
    model_path = r"F:/01_Univalle/01_TG/01_Python/bird_species_resnet18.pth"
    species_model, class_names = load_species_model(model_path, device)

    # Classify crops
    classified_results = classify_crops_batch(
        species_model,
        class_names,
        crop_batches,
        device,
        conf_threshold = 0.7    # minimum confidence threshold for keeping a classification
    )

    print("\n[DEBUG] Classified results:")
    for i, r in enumerate(classified_results):
        print(
            f"  result #{i}: "
            f"det_score={r['detector_score']:.3f} "
            f"species={r['pred_species']} "
            f"cls_conf={r['pred_conf']:.3f} "
            f"keep={r['keep']} "
            f"box={r['padded_box']}"
        )

    # 5. Draw boxes on the original image
    annotated = annotate_full_image(pil_img, classified_results)
    # annotated_pil = Image.fromarray(annotated)

    # # # 6. Show the annotated image
    output_path = Path(r"G:/Usuarios/Daniel/Descargas/imagen_1.png")
    annotated.save(output_path)
    os.startfile(output_path) 

    return annotated, classified_results

if __name__ == "__main__":
    mask_or_bbox = "bbox"
    img_path = "F:/01_Univalle/01_TG/nuevo_dataset_split/test/colibri_delphinae/ML641964967.jpg"
    main(mask_or_bbox, img_path)
    
