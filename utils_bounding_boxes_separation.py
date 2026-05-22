from PIL import Image
# import torch
from torchvision import transforms
# import math


def expand_box(box, padding_ratio, img_w, img_h):
    """
    Expand [x1,y1,x2,y2] by padding_ratio on each side and clip to image bounds.
    Returns [x1,y1,x2,y2] as ints.
    """
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1

    pad_w = w * padding_ratio
    pad_h = h * padding_ratio

    new_x1 = max(0,           x1 - pad_w)
    new_y1 = max(0,           y1 - pad_h)
    new_x2 = min(img_w - 1.0, x2 + pad_w)
    new_y2 = min(img_h - 1.0, y2 + pad_h)

    return [int(new_x1), int(new_y1), int(new_x2), int(new_y2)]


def crop_box(pil_img, box_xyxy):
    """
    Crop a PIL image given [x1,y1,x2,y2] (ints).
    Returns a new PIL.Image with the cropped region
    """
    x1, y1, x2, y2 = box_xyxy
    return pil_img.crop((x1, y1, x2, y2))


def letterbox_and_to_tensor(pil_img, size=224):
    """
    Prepare a crop for the classifier while preserving aspect ratio.

    1. Resize so the longest side == size
    2. Pad the shorter side with black pixels so result is size x size
    3. Convert to normalized tensor ready for classifier

    Returns:
        tensor: shape [3, size, size] (already normalized)
        padded_pil: padded/resized PIL (optional for visualization/debug)
    """

    # 1. Get original crop size
    w, h = pil_img.size
    if w == 0 or h == 0:
        raise ValueError("Invalid crop with zero width or height")

    # scale keeping aspect ratio
    # longest side -> size
    scale = size / max(w, h)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    resized = pil_img.resize((new_w, new_h), Image.BILINEAR)

    # 2. Create square canvas and paste the resized image
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    offset_x = (size - new_w) // 2
    offset_y = (size - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y))

    # 3. To tensor and normalize same as classifier training
    tfm = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(
            mean=[0.485, 0.456, 0.406],
            std=[0.229, 0.224, 0.225],
        )
    ])

    tensor = tfm(canvas)  # shape [3, size, size]

    return tensor, canvas


def prepare_crops_for_classifier(pil_img, detections, padding_ratio=0.10, min_conf=0.0):
    """
    Convert detector outputs (boxes + scores) into classifier-ready crops
    Given:
        pil_img: original full image (PIL.Image)
        detections: list of dicts with keys:
            - "box": [x1,y1,x2,y2] (float coords)
            - "score": float
            - "label": str (e.g. "bird", "potted plant", etc.)
        padding_ratio: how much to expand each box on each side
        min_conf: skip detections with score < min_conf

    Returns:
        crops_info: list of dicts:
            {
                "tensor": torch.FloatTensor [3,224,224] normalized,
                "padded_box": [x1,y1,x2,y2],
                "score": float,
                "label": str,
                "debug_image": PIL.Image (padded 224x224 crop)
            }
    """
    img_w, img_h = pil_img.size
    results = []

    for det in detections:
        score = det["score"]
        if score < min_conf:
            continue

        raw_box = det["box"]  # [x1,y1,x2,y2] float
        padded_box = expand_box(raw_box, padding_ratio, img_w, img_h)
        crop_pil = crop_box(pil_img, padded_box)

        tensor_224, debug_img_224 = letterbox_and_to_tensor(crop_pil, size=224)

        results.append({
            "tensor": tensor_224,              # ready for classifier
            "padded_box": padded_box,          # location on original image
            "score": score,                    # detector confidence
            "label": det["label"],             # detector label ("bird", "vase", ...)
            "debug_image": debug_img_224,      # for human inspection / plotting
        })

    return results
