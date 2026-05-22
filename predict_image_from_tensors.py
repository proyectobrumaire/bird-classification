import torch
from torchvision import models
# import matplotlib.pyplot as plt

def load_species_model(model_path, device):
    """
    Load the trained species classifier from disk.
    Returns (model, class_names).
    """
    checkpoint = torch.load(model_path, map_location=device)
    class_names = checkpoint["class_names"]

    num_classes = len(class_names)
    model = models.resnet18(weights=None)
    in_features = model.fc.in_features
    model.fc = torch.nn.Linear(in_features, num_classes)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.to(device)
    model.eval()

    return model, class_names

def classify_tensor(model, class_names, crop_tensor, device, return_probs=False):
    """
    Classify a single crop that is already normalized to:
      - shape [3,224,224]
      - same normalization mean/std as training

    Returns (predicted_class_name, confidence_float) or
    (predicted_class_name, confidence, probs_vector)
    """
    with torch.no_grad():
        # add batch dimension -> [1,3,224,224]
        crop_tensor = crop_tensor.unsqueeze(0).to(device)

        # CLASSIFICATION STAGE
        outputs = model(crop_tensor)                        # [1, num_classes]
        probs = torch.nn.functional.softmax(outputs, dim=1) # [1, num_classes]
        probs = probs[0]                                    # [num_classes]

        top_prob, top_idx = torch.max(probs, dim=0)

    predicted_name = class_names[top_idx.item()]
    confidence = top_prob.item()

    if return_probs:
        return (predicted_name, confidence, probs.cpu().numpy())
    else:
        return (predicted_name, confidence)

def classify_crops_batch(model, class_names, crop_batches, device, conf_threshold=None):
    """
    Classify all crops produced by prepare_crops_for_classifier(...).
    Each element looks like:
    {
        "tensor": torch.FloatTensor [3,224,224] normalized,
        "padded_box": [x1,y1,x2,y2],
        "score": detector_score,
        "label": detector_label,
        "debug_image": PIL.Image (224x224 padded crop)
    }

    It will run classify_tensor() on each "tensor".
    It returns a list of dicts:
    {
        "pred_species": str,
        "pred_conf": float,
        "padded_box": [x1,y1,x2,y2],
        "detector_label": str,
        "detector_score": float,
        "keep": bool   # if conf_threshold is set, True means above threshold
        "debug_image":   PIL.Image
    }
    """
    results = []
    for crop_info in crop_batches:
        crop_tensor = crop_info["tensor"]  # [3,224,224] normalized
        species_name, confidence = classify_tensor(
            model,
            class_names,
            crop_tensor,
            device
        )

        keep_flag = True
        if conf_threshold is not None:
            keep_flag = (confidence >= conf_threshold)

        results.append({
            "pred_species": species_name,
            "pred_conf": confidence,
            "padded_box": crop_info["padded_box"],
            "detector_label": crop_info["label"],
            "detector_score": crop_info["score"],
            "keep": keep_flag,
            "debug_image": crop_info["debug_image"],  # for visualization
        })

    return results

# def show_classified_crop(crop_debug_image, species_name, confidence):
#     """
#     Show the 224x224 padded crop with the predicted species + confidence.
#     crop_debug_image: PIL.Image from crop_info["debug_image"]
#     """
#     plt.figure(figsize=(4,4))
#     plt.imshow(crop_debug_image)
#     plt.axis("off")
#     plt.title(f"{species_name} ({confidence*100:.1f}%)")
#     plt.tight_layout()
#     plt.show()

def annotate_full_image(pil_img, classified_results):
    """
    Create a copy of the full original image and draw:
    - bounding box for each kept detection
    - species name + confidence
    """
    from PIL import ImageDraw, ImageFont
    annotated = pil_img.copy()
    draw = ImageDraw.Draw(annotated)

    try:
        font = ImageFont.load_default()
    except:
        font = None

    for item in classified_results:
        if not item["keep"]:
            continue

        x1, y1, x2, y2 = item["padded_box"]
        species = item["pred_species"]
        conf    = item["pred_conf"]

        # box
        draw.rectangle([(x1, y1), (x2, y2)], outline="lime", width=3)

        # label background
        text = f"{species} {conf*100:.1f}%"
        text_w = 7 * len(text) + 10
        text_h = 20
        text_bg = (x1, y1, x1 + text_w, y1 + text_h)
        draw.rectangle(text_bg, fill="lime")

        # text
        draw.text((x1 + 5, y1 + 4), text, fill="black", font=font)

    return annotated
