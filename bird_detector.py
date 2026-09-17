import torch
from torchvision import transforms
from torchvision.models.detection import fasterrcnn_resnet50_fpn, FasterRCNN_ResNet50_FPN_Weights, maskrcnn_resnet50_fpn, MaskRCNN_ResNet50_FPN_Weights
from pathlib import Path
from typing import List, Tuple
from PIL import Image
from utils_crop_segmentation import apply_mask_to_crop

def pick_file_dialog():
    """
    Open a file dialog to choose an image interactively.
    Returns a Path or None if the user cancels.
    """
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    filename = filedialog.askopenfilename(
        title="Select an image to run bird detection",
        filetypes=[
            ("Image files", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
            ("All files", "*.*"),
        ],
    )
    if not filename:
        return None
    return Path(filename)


def load_detector(device):
    """
    Load a Faster R-CNN model pretrained on COCO.
    Returns (model, coco_class_names).
    """
    weights = FasterRCNN_ResNet50_FPN_Weights.COCO_V1
    model = fasterrcnn_resnet50_fpn(weights=weights)
    model.to(device)
    model.eval()

    # COCO class labels (index -> string); provided by weights.meta["categories"]
    coco_classes = weights.meta["categories"]
    # In COCO, "bird" is one of the classes
    return model, coco_classes

def load_maskrcnn_detector(device):
    """
    Load a Mask R-CNN model pretrained on COCO.
    Returns (model, coco_class_names).

    This model can give:
      - bounding boxes
      - class labels
      - scores
      - segmentation masks
    """
    weights = MaskRCNN_ResNet50_FPN_Weights.COCO_V1
    model = maskrcnn_resnet50_fpn(weights=weights)
    model.to(device)
    model.eval()

    coco_classes = weights.meta["categories"]
    return model, coco_classes


def run_maskrcnn_on_image(model, coco_classes, image_tensor, target_class="bird", conf_threshold=0.7):
    """
    Run Mask R-CNN on a single image tensor.

    Returns a list of dicts:
        {
            "box":  [x1, y1, x2, y2],
            "score": float,
            "label": str,
            "mask":  torch.Tensor(H, W) with values in [0,1]
        }

    Filtered to the target_class and score >= conf_threshold.
    """
    model.eval()
    with torch.no_grad():
        outputs = model([image_tensor])[0]

    boxes = outputs["boxes"]        # [N,4]
    labels = outputs["labels"]      # [N]
    scores = outputs["scores"]      # [N]
    masks  = outputs["masks"]       # [N,1,H,W]

    results = []

    for box, label, score, mask in zip(boxes, labels, scores, masks):
        class_name = coco_classes[label.item()]
        score_val = score.item()

        if class_name == target_class and score_val >= conf_threshold:
            # mask: [1,H,W] -> [H,W], still on device
            mask_hw = mask[0]      # shape [H,W]
            results.append({
                "box":   box.cpu().tolist(),
                "score": score_val,
                "label": class_name,
                "mask":  mask_hw.cpu(),  # keep as tensor on CPU for later
            })

    return results

def preprocess_image_for_detector(pil_img, device):
    """
    Convert a PIL image into the tensor format expected by the detector.

    - Detection models in torchvision typically expect only ToTensor()
      (no mean/std normalization)

    Returns
    -------
    tensor : torch.Tensor
        Image tensor on the specified device, shape [C, H, W], range [0, 1].
    """
    transform = transforms.ToTensor()  # -> [C,H,W], float32, range [0,1]
    tensor = transform(pil_img).to(device)
    return tensor


def run_detector_on_image(model, coco_classes, image_tensor, target_class="bird", conf_threshold=0.7):
    """
    Run model on a single image tensor.
    Returns a list of dicts: 
    Each dict has keys:
        - 'box'   : [x1, y1, x2, y2]
        - 'score' : float, detector confidence
        - 'label' : str, class name

    filtered to only the target_class ("bird") and score >= conf_threshold.
    """
    with torch.no_grad():
        outputs = model([image_tensor])[0]

    boxes = outputs["boxes"]        # [N,4]
    labels = outputs["labels"]      # [N]
    scores = outputs["scores"]      # [N]

    results = []

    for box, label, score in zip(boxes, labels, scores):
        class_name = coco_classes[label.item()]
        if class_name == target_class and score.item() >= conf_threshold:
            results.append({
                "box": box.cpu().tolist(),      # [x1,y1,x2,y2]
                "score": score.item(),          # confidence score
                "label": class_name
            })

    return results

# ------------------------------------------------------------------------------------------

import shutil
from tqdm import tqdm
from PIL import ImageDraw

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

def process_folder_dataset(
    input_root: Path,
    output_root: Path,
    use_maskrcnn: bool = False,
    conf_threshold: float = 0.7,
    target_class: str = "bird",
    mode: str = "crop",          # "crop" or "draw"
    copy_if_no_det: bool = True
):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if use_maskrcnn:
        model, coco_classes = load_maskrcnn_detector(device)
    else:
        model, coco_classes = load_detector(device)

    output_root.mkdir(parents=True, exist_ok=True)

    img_paths = [p for p in input_root.rglob("*") if p.is_file() and p.suffix.lower() in IMG_EXTS]

    for img_path in tqdm(img_paths, desc="Detecting"):
        rel = img_path.relative_to(input_root)
        out_path = output_root / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)

        pil_img = Image.open(img_path).convert("RGB")
        img_t = preprocess_image_for_detector(pil_img, device)

        if use_maskrcnn:
            dets = run_maskrcnn_on_image(model, coco_classes, img_t, target_class, conf_threshold)
        else:
            dets = run_detector_on_image(model, coco_classes, img_t, target_class, conf_threshold)

        if not dets:
            if copy_if_no_det:
                shutil.copy2(img_path, out_path)
            continue

        stem = out_path.stem
        ext = out_path.suffix

        for i, det in enumerate(dets):
            x1, y1, x2, y2 = map(int, det["box"])

            new_out_path = out_path.with_name(f"{stem}_b{i}{ext}")

            if mode == "crop":
                crop = pil_img.crop((x1, y1, x2, y2))
                crop.save(new_out_path)
            else:  # mode == "draw"
                img2 = pil_img.copy()
                draw = ImageDraw.Draw(img2)
                draw.rectangle([x1, y1, x2, y2], width=4)
                img2.save(new_out_path)

if __name__ == "__main__":
    input_root = Path(r"F:/01_Univalle/01_TG/nuevo_dataset_split")          # <-- your dataset root (has subfolders)
    output_root = Path(r"F:/01_Univalle/01_TG/dataset_bbox")    # <-- new dataset root

    process_folder_dataset(
        input_root=input_root,
        output_root=output_root,
        use_maskrcnn=False,   # True if you want Mask R-CNN
        conf_threshold=0.7,
        mode="crop",          # "crop" or "draw"
        copy_if_no_det=True
    )

# ----------------------------------------------------------------------------------------------

# def run_detector_on_image_debug(model, coco_classes, image_tensor, min_score_to_show=0.01):
    # """
    # Debug function:
    # - Returns ALL detections (all classes), not just 'bird'.
    # - Does not filter by confidence except a very low floor.
    # """
    # model.eval()
    # with torch.no_grad():
    #     outputs = model([image_tensor])[0]

    # boxes = outputs["boxes"]        # [N,4]
    # labels = outputs["labels"]      # [N]
    # scores = outputs["scores"]      # [N]

    # results = []

    # for box, label, score in zip(boxes, labels, scores):
    #     score_val = score.item()
    #     if score_val < min_score_to_show:
    #         continue  # ignore super tiny scores

    #     class_name = coco_classes[label.item()]
    #     x1, y1, x2, y2 = box.cpu().tolist()

    #     results.append({
    #         "box": [x1, y1, x2, y2],
    #         "score": score_val,
    #         "label": class_name,
    #     })

    # return results

# def draw_boxes(pil_img, detections):
#     """
#     Draw bounding boxes and labels on a copy of the image
#     Returns a new PIL image with overlays (new image with rectangles and text drawn on top)
#     """
#     img_draw = pil_img.copy()
#     draw = ImageDraw.Draw(img_draw)

#     # Optional: try to load a default font
#     try:
#         font = ImageFont.load_default()
#     except:
#         font = None

#     for det in detections:
#         x1, y1, x2, y2 = det["box"]
#         score = det["score"]

#         # Draw the rectangle
#         draw.rectangle([(x1, y1), (x2, y2)], outline="red", width=3)

#         # Label text
#         text = f"{det['label']} {score*100:.1f}%"
#         text_pos = (x1 + 5, y1 + 5)
#         text_bg_pos = (x1, y1, x1 + 5 + len(text)*7, y1 + 20)

#         draw.rectangle(text_bg_pos, fill="red")
#         draw.text(text_pos, text, fill="white", font=font)

#     return img_draw


# def show_image(pil_img, title="Detections"):
#     """
#     Display image with matplotlib (so you get a pop-up window).
#     """
#     plt.figure(figsize=(8, 8))
#     plt.imshow(pil_img)
#     plt.axis("off")
#     plt.title(title)
#     plt.tight_layout()
#     plt.show()


# def main():
#     # Pick image interactively
#     image_path = pick_file_dialog()
#     if image_path is None:
#         print("[INFO] No image selected. Exiting.")
#         return

#     print(f"[INFO] Selected image: {image_path}")

#     # Device
#     device = "cuda" if torch.cuda.is_available() else "cpu"
#     print(f"[INFO] Using device: {device}")

#     # Load image
#     pil_img = Image.open(image_path).convert("RGB")

#     # Load detector
#     model, coco_classes = load_detector(device)

#     # Preprocess
#     image_tensor = preprocess_image_for_detector(pil_img, device)

#     # Detect birds only
#     detections = run_detector_on_image(
#         model,
#         coco_classes,
#         image_tensor,
#         target_class="bird",
#         conf_threshold=0.8  # you can increase this to 0.85 for stricter precision
#     )

#     print(f"[INFO] Found {len(detections)} bird candidates:")
#     for det in detections:
#         print(f"  box={det['box']}, score={det['score']:.3f}")

#     # Draw boxes
#     boxed_img = draw_boxes(pil_img, detections)

#     # Show result
#     show_image(boxed_img, title="Detected birds (COCO Faster R-CNN)")

# def main_all():
#     # Pick image interactively
#     image_path = pick_file_dialog()
#     if image_path is None:
#         print("[INFO] No image selected. Exiting.")
#         return

#     print(f"[INFO] Selected image: {image_path}")

#     # Device
#     device = "cuda" if torch.cuda.is_available() else "cpu"
#     print(f"[INFO] Using device: {device}")

#     # Load image
#     pil_img = Image.open(image_path).convert("RGB")

#     # Load detector
#     model, coco_classes = load_detector(device)

#     # Preprocess
#     image_tensor = preprocess_image_for_detector(pil_img, device)

#     detections = run_detector_on_image_debug(
#     model,
#     coco_classes,
#     image_tensor,
#     min_score_to_show=0.01
# )

#     from utils_bounding_boxes_separation import prepare_crops_for_classifier

#     # ...

#     # detections = [...] from your detector (could be only 'bird' detections,
#     # or ALL detections if you're in debug mode)

#     crop_batches = prepare_crops_for_classifier(
#         pil_img=pil_img,
#         detections=detections,
#         padding_ratio=0.10,
#         min_conf=0.05,  # keep detections above 5% confidence for now
#     )

#     print(f"[INFO] Prepared {len(crop_batches)} crops for classification.")

#     # crop_batches is now a list. Each element has:
#     #   "tensor"       -> torch tensor [3,224,224] normalized
#     #   "padded_box"   -> [x1,y1,x2,y2] on the original image
#     #   "score"        -> detector's confidence
#     #   "label"        -> detector label ("bird", "vase", "potted plant")
#     #   "debug_image"  -> the 224x224 padded crop as a PIL image (useful to visualize)


#     print("[INFO] Raw detections (all classes):")
#     for det in detections:
#         print(f"  {det['label']}  {det['score']*100:.1f}%  box={det['box']}")

#     # Draw boxes
#     boxed_img = draw_boxes(pil_img, detections)

#     # Show result
#     show_image(boxed_img, title="Detected birds (COCO Faster R-CNN)")

# # if __name__ == "__main__":
# #     main_all()

# if __name__ == "__main__":
#     main()