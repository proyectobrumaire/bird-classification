import numpy as np
from PIL import Image
import torch


def apply_mask_to_crop(
    pil_img: Image.Image,
    mask_hw: torch.Tensor,
    box,
    style: str = "black",
) -> Image.Image:
    """
    Apply an instance mask to the region defined by `box` in `pil_img`.

    Parameters
    ----------
    pil_img : PIL.Image.Image
        Original RGB image.
    mask_hw : torch.Tensor
        Float tensor [H, W] with values in [0,1] for the whole image.
    box : list[float]
        [x1, y1, x2, y2] in image coordinates.
    style : str
        "transparent"  -> S1, RGBA output, background alpha=0
        "black"        -> S2, RGB output, background=0

    Returns
    -------
    PIL.Image.Image
        The cropped bird, either RGBA (transparent background) or RGB (black background).
    """
    # Ensure box is ints and within image bounds
    W, H = pil_img.size
    x1, y1, x2, y2 = box
    x1 = max(0, min(int(x1), W - 1))
    y1 = max(0, min(int(y1), H - 1))
    x2 = max(x1 + 1, min(int(x2), W))
    y2 = max(y1 + 1, min(int(y2), H))

    # Crop the original image
    crop = pil_img.crop((x1, y1, x2, y2))  # RGB

    # Convert mask to numpy [H,W], threshold to binary
    mask_np = (mask_hw.numpy() > 0.5).astype(np.uint8)  # 0 or 1
    # Crop the mask to the same region
    mask_crop = mask_np[y1:y2, x1:x2]  # [h, w]

    if style == "transparent":
        # S1: Transparent background (RGBA)
        crop_rgba = crop.convert("RGBA")
        # Create alpha channel from mask
        alpha = (mask_crop * 255).astype(np.uint8)  # 0 or 255
        alpha_img = Image.fromarray(alpha, mode="L")
        crop_rgba.putalpha(alpha_img)
        return crop_rgba

    else:
        # S2: Black background (RGB)
        crop_np = np.array(crop).astype(np.uint8)  # [h,w,3]
        # Expand mask_crop to 3 channels: [h,w] -> [h,w,1]
        mask_3 = mask_crop[..., None]             # [h,w,1]
        # Zero background
        crop_masked = crop_np * mask_3           # background -> 0
        crop_masked_img = Image.fromarray(crop_masked, mode="RGB")
        return crop_masked_img
