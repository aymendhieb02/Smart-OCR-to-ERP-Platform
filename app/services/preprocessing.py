import cv2
import numpy as np


def preprocess_image(image: np.ndarray, profile: str = "current", max_side: int | None = None) -> np.ndarray:
    resized = resize_for_ocr(image, max_side=max_side)
    normalized_profile = (profile or "current").strip().lower()
    if normalized_profile in {"direct", "none"}:
        return resized
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY) if len(resized.shape) == 3 else resized
    if normalized_profile == "grayscale":
        return gray
    if normalized_profile == "contrast":
        return improve_contrast(gray)
    if normalized_profile == "minimal":
        enhanced = improve_contrast(gray)
        return cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    denoised = cv2.fastNlMeansDenoising(gray, None, h=12, templateWindowSize=7, searchWindowSize=21)
    enhanced = improve_contrast(denoised)
    deskewed = deskew(enhanced)
    return cv2.adaptiveThreshold(
        deskewed,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )


def preprocess_table_region(image: np.ndarray, profile: str = "current", max_side: int | None = None) -> np.ndarray:
    resized = resize_for_ocr(image, min_width=900, max_side=max_side)
    normalized_profile = (profile or "current").strip().lower()
    if normalized_profile in {"direct", "none"}:
        return resized
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY) if len(resized.shape) == 3 else resized
    if normalized_profile == "grayscale":
        return gray
    if normalized_profile == "contrast":
        return improve_contrast(gray)
    if normalized_profile == "minimal":
        enhanced = improve_contrast(gray)
        return cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    enhanced = improve_contrast(gray)
    blurred = cv2.GaussianBlur(enhanced, (3, 3), 0)
    _, thresholded = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1))
    return cv2.morphologyEx(thresholded, cv2.MORPH_OPEN, kernel)


def resize_for_ocr(image: np.ndarray, min_width: int = 1400, max_side: int | None = None) -> np.ndarray:
    height, width = image.shape[:2]
    if max_side and max(height, width) > max_side:
        scale = max_side / max(height, width)
        return cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    if width >= min_width:
        return image
    scale = min_width / max(width, 1)
    return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_CUBIC)


def improve_contrast(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def deskew(gray: np.ndarray) -> np.ndarray:
    inverted = cv2.bitwise_not(gray)
    coords = np.column_stack(np.where(inverted > 0))
    if coords.size == 0:
        return gray

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.3 or abs(angle) > 15:
        return gray

    h, w = gray.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
