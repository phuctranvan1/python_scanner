import cv2  # type: ignore
import numpy as np  # type: ignore
from PIL import Image, ImageEnhance, ImageFilter, ImageOps  # type: ignore

def preprocess_image(img):
    """
    Preprocessing pipeline tối ưu cho hai loại screenshot điện thoại:
    - Loại 1: Nền trắng, chữ đen, layout dọc (giao diện web)
    - Loại 2: Nền trắng/xanh lá, text đen, layout key:value (VETC app)
    """
    open_cv_image = np.array(img)

    # Convert to grayscale
    if len(open_cv_image.shape) == 3:
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2GRAY)
    else:
        gray = open_cv_image.copy()

    h, w = gray.shape

    # Upscale nhẹ nếu ảnh nhỏ
    if w < 800:
        scale = 2
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)

    # Phân tích độ sáng trung bình để phát hiện nền tối / sáng
    mean_brightness = np.mean(gray)

    if mean_brightness > 180:
        kernel = np.array([[0, -0.5, 0],
                            [-0.5, 3, -0.5],
                            [0, -0.5, 0]])
        enhanced = cv2.filter2D(gray, -1, kernel)
        enhanced = np.clip(enhanced, 0, 255).astype(np.uint8)
    else:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        enhanced = cv2.bilateralFilter(enhanced, d=5, sigmaColor=50, sigmaSpace=50)

    binary = cv2.adaptiveThreshold(
        enhanced, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,
        C=10
    )

    return Image.fromarray(binary)

def preprocess_for_easyocr(img):
    """Preprocessing riêng cho EasyOCR: giữ màu xám (không binarize)"""
    open_cv_image = np.array(img)
    if len(open_cv_image.shape) == 3:
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2GRAY)
    else:
        gray = open_cv_image.copy()

    h, w = gray.shape
    if w < 800:
        gray = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)

    mean_brightness = np.mean(gray)
    if mean_brightness > 180:
        kernel = np.array([[0, -0.5, 0], [-0.5, 3, -0.5], [0, -0.5, 0]])
        enhanced = cv2.filter2D(gray, -1, kernel)
        enhanced = np.clip(enhanced, 0, 255).astype(np.uint8)
    else:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

    return Image.fromarray(enhanced)
