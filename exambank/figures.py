# -*- coding: utf-8 -*-
"""رندر صفحه و برش شکل‌ها.

قرارداد مختصات Gemini (تأییدشده با مستندات رسمی ai.google.dev، Vision/Bounding boxes):
    box_2d = [ymin, xmin, ymax, xmax]  با مقیاس 0..1000 نسبت به ابعاد تصویر.
این قرارداد برای «تصویر» مستند شده؛ برای ورودی PDF چارچوب مختصات (کل صفحه یا ناحیه‌ی محتوا) در
مستندات تصریح نشده است. پس هر bbox اعتبارسنجی می‌شود و اگر نامطمئن بود کل صفحه ذخیره می‌شود
(bbox_status = full_page_fallback). مرحله‌ی چشمی در smoke_test.md این را روی PDF واقعی بررسی می‌کند.
"""
from pathlib import Path

MIN_AREA_FRACTION = 0.01   # برش کوچک‌تر از ۱٪ صفحه مشکوک است
PAD_FRACTION = 0.01        # حاشیه‌ی امن دور برش (نسبت به ابعاد صفحه)


def bbox_to_pixels(box_2d, width, height):
    """-> (left, top, right, bottom) به پیکسل، یا None اگر bbox نامعتبر/مشکوک باشد."""
    if not isinstance(box_2d, (list, tuple)) or len(box_2d) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(v) for v in box_2d]
    except (TypeError, ValueError):
        return None
    if not all(0 <= v <= 1000 for v in (ymin, xmin, ymax, xmax)):
        return None
    if ymin >= ymax or xmin >= xmax:
        return None
    if (ymax - ymin) * (xmax - xmin) / 1_000_000 < MIN_AREA_FRACTION:
        return None
    pad = PAD_FRACTION * 1000
    ymin, xmin = max(0, ymin - pad), max(0, xmin - pad)
    ymax, xmax = min(1000, ymax + pad), min(1000, xmax + pad)
    return (int(xmin / 1000 * width), int(ymin / 1000 * height),
            int(round(xmax / 1000 * width)), int(round(ymax / 1000 * height)))


def render_page(pdf_path, page_number, scale=2.0):
    """رندر صفحه‌ی page_number (۱-مبنا) به تصویر PIL با pypdfium2."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page = pdf[page_number - 1]
        return page.render(scale=scale).to_pil().convert("RGB")
    finally:
        pdf.close()


def save_figures(pdf_path, pdf_key, page_number, figures, out_dir, scale=2.0):
    """figures: لیست دیکشنری از extract (با box_2d). خروجی: همان لیست با figure_id، image_path، bbox_status.
    figure_id توسط کد ساخته می‌شود (پایدار)، نه LLM: <pdf>-p<page>-f<n>."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not figures:
        return []
    img = render_page(pdf_path, page_number, scale)
    w, h = img.size
    full_name = f"{pdf_key}_p{page_number:04d}_page.png"
    full_saved = False
    result = []
    for n, fig in enumerate(figures, start=1):
        fid = f"{pdf_key}-p{page_number}-f{n}"
        px = bbox_to_pixels(fig.get("box_2d"), w, h)
        rec = dict(fig)
        rec["figure_id"] = fid
        rec["is_ai_description"] = True  # توضیح فارسی فقط برای جستجو است و مبنای صحیح/غلط نیست
        if px is not None:
            name = f"{pdf_key}_p{page_number:04d}_f{n}.png"
            img.crop(px).save(out_dir / name)
            rec.update(image_path=f"figures/{name}", bbox_status="cropped", bbox_pixels=list(px))
        else:
            if not full_saved:
                img.save(out_dir / full_name)
                full_saved = True
            rec.update(image_path=f"figures/{full_name}", bbox_status="full_page_fallback", bbox_pixels=None)
        result.append(rec)
    return result
