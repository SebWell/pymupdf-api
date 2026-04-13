"""
API d'extraction de texte PDF avec PyMuPDF
Extraction rapide de texte depuis des PDF natifs (non scannés)
Inclut PyMuPDF4LLM pour extraction Markdown structuree
"""

from flask import Flask, request, jsonify
import fitz  # PyMuPDF
import pymupdf4llm
import io
import base64
import os
import gc
import re
import time
import tempfile
import requests as http_requests

app = Flask(__name__)

# URL des services OCR (configurable via env vars)
PADDLEOCR_URL = os.getenv("PADDLEOCR_URL", "http://paddleocr:5000")
MARKER_URL = os.getenv("MARKER_URL", "http://marker:5000")

# Batch size pour OCR page par page
OCR_BATCH_SIZE = int(os.getenv("OCR_BATCH_SIZE", "10"))

# Timeout par page pour PaddleOCR (secondes)
OCR_PAGE_TIMEOUT = int(os.getenv("OCR_PAGE_TIMEOUT", "60"))

# Seuil de pages pour reduire le DPI
LARGE_PDF_THRESHOLD = int(os.getenv("LARGE_PDF_THRESHOLD", "30"))

# Limite max de pages pour OCR scanned (au-dela, timeout infra)
MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "80"))


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "PyMuPDF PDF Text Extraction API",
        "version": "3.0.0",
        "description": "Extraction de texte et Markdown structure depuis PDFs natifs et scannes",
        "endpoints": {
            "/extract": "POST - Extraire le texte brut d'un PDF",
            "/extract-markdown": "POST - Extraire le texte au format Markdown structure",
            "/ocr-scanned-pdf": "POST - OCR page par page (split + PaddleOCR par image + merge)",
            "/info": "POST - Obtenir les metadonnees d'un PDF",
            "/health": "GET - Verifier l'etat du service"
        }
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "engine": "PyMuPDF",
        "version": fitz.version[0]
    })


def get_pdf_from_request():
    """Récupère le PDF depuis la requête (fichier ou base64)"""
    pdf_bytes = None

    # Option 1: Fichier uploadé
    if "file" in request.files:
        file = request.files["file"]
        pdf_bytes = file.read()

    # Option 2: Base64 dans JSON
    elif request.is_json and "file" in request.json:
        file_data = request.json["file"]
        # Retirer le préfixe data:application/pdf si présent
        if "," in file_data:
            file_data = file_data.split(",")[1]
        pdf_bytes = base64.b64decode(file_data)

    # Option 3: URL dans JSON ou FormData — télécharger le fichier
    elif request.is_json and "url" in request.json:
        import requests as req
        url = request.json["url"]
        resp = req.get(url, timeout=120)
        if resp.status_code == 200:
            pdf_bytes = resp.content
    elif "url" in request.form:
        import requests as req
        url = request.form["url"]
        resp = req.get(url, timeout=120)
        if resp.status_code == 200:
            pdf_bytes = resp.content

    return pdf_bytes


def is_native_pdf(doc):
    """
    Détecte si le PDF contient du texte natif ou s'il s'agit d'un scan
    Retourne un score de 0 à 1 (1 = PDF natif avec texte, 0 = scan/images)
    """
    total_pages = len(doc)
    pages_with_text = 0
    total_text_length = 0

    for page in doc:
        text = page.get_text().strip()
        if len(text) > 50:  # Au moins 50 caractères pour considérer comme page avec texte
            pages_with_text += 1
        total_text_length += len(text)

    if total_pages == 0:
        return 0, "empty"

    text_ratio = pages_with_text / total_pages
    avg_text_per_page = total_text_length / total_pages

    if text_ratio > 0.8 and avg_text_per_page > 100:
        return 1.0, "native"
    elif text_ratio > 0.3 or avg_text_per_page > 50:
        return text_ratio, "mixed"
    else:
        return 0.0, "scanned"


@app.route("/extract", methods=["POST"])
def extract():
    """
    Extrait le texte d'un PDF

    Accepte:
    - multipart/form-data avec fichier 'file'
    - JSON avec 'file' en base64

    Paramètres optionnels:
    - pages: pages spécifiques à extraire (ex: "1,2,5" ou "1-5")
    - format: "text" (défaut) ou "blocks" (avec positions)
    """
    try:
        pdf_bytes = get_pdf_from_request()

        if not pdf_bytes:
            return jsonify({
                "error": "Aucun fichier PDF fourni",
                "usage": "Envoyez un PDF via 'file' (multipart) ou en base64 (JSON)"
            }), 400

        # Paramètres
        pages_param = request.form.get("pages") or request.args.get("pages")
        output_format = request.form.get("format") or request.args.get("format") or "text"

        # Ouvrir le PDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)

        # Déterminer les pages à extraire
        pages_to_extract = list(range(total_pages))
        if pages_param:
            pages_to_extract = parse_pages(pages_param, total_pages)

        # Détecter si PDF natif ou scanné
        native_score, pdf_type = is_native_pdf(doc)

        # Extraire le texte
        if output_format == "blocks":
            result = extract_blocks(doc, pages_to_extract)
        else:
            result = extract_text(doc, pages_to_extract)

        doc.close()

        return jsonify({
            "success": True,
            "text": result["text"],
            "pages_count": total_pages,
            "pages_extracted": len(pages_to_extract),
            "characters_count": len(result["text"]),
            "pdf_type": pdf_type,
            "native_score": round(native_score, 2),
            "needs_ocr": pdf_type == "scanned",
            "pages_detail": result.get("pages_detail") if output_format == "blocks" else None
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/extract-markdown", methods=["POST"])
def extract_markdown():
    """
    Extrait le texte d'un PDF natif au format Markdown structure.
    Utilise PyMuPDF4LLM pour detecter les titres (via taille de police)
    et generer une structure hierarchique avec #, ##, ###.

    Accepte:
    - multipart/form-data avec fichier 'file'
    - JSON avec 'file' en base64

    Retourne:
    - markdown: Texte au format Markdown avec structure
    - has_structure: True si des titres (#) ont ete detectes
    - source: "pymupdf4llm"
    """
    try:
        pdf_bytes = get_pdf_from_request()

        if not pdf_bytes:
            return jsonify({
                "error": "Aucun fichier PDF fourni",
                "usage": "Envoyez un PDF via 'file' (multipart) ou en base64 (JSON)"
            }), 400

        # Verifier d'abord si le PDF est natif ou scanne
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        native_score, pdf_type = is_native_pdf(doc)
        total_pages = len(doc)
        doc.close()

        # Si PDF scanne, retourner une indication pour utiliser Marker
        if pdf_type == "scanned":
            return jsonify({
                "success": True,
                "markdown": "",
                "source": "pymupdf4llm",
                "has_structure": False,
                "needs_ocr": True,
                "pdf_type": pdf_type,
                "native_score": round(native_score, 2),
                "pages_count": total_pages,
                "message": "PDF scanne detecte. Utilisez Marker API pour OCR + structure."
            })

        # Sauvegarder temporairement pour pymupdf4llm
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            # Extraction avec structure markdown
            markdown_text = pymupdf4llm.to_markdown(tmp_path)

            # Compter les headers pour verifier la structure
            h1_count = markdown_text.count('\n# ')
            h2_count = markdown_text.count('\n## ')
            h3_count = markdown_text.count('\n### ')
            has_structure = (h1_count + h2_count + h3_count) > 0

            return jsonify({
                "success": True,
                "markdown": markdown_text,
                "source": "pymupdf4llm",
                "has_structure": has_structure,
                "needs_ocr": False,
                "pdf_type": pdf_type,
                "native_score": round(native_score, 2),
                "pages_count": total_pages,
                "structure_stats": {
                    "h1_count": h1_count,
                    "h2_count": h2_count,
                    "h3_count": h3_count
                }
            })

        finally:
            os.unlink(tmp_path)

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/info", methods=["POST"])
def info():
    """
    Retourne les métadonnées d'un PDF
    """
    try:
        pdf_bytes = get_pdf_from_request()

        if not pdf_bytes:
            return jsonify({
                "error": "Aucun fichier PDF fourni"
            }), 400

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        metadata = doc.metadata

        # Détecter si PDF natif ou scanné
        native_score, pdf_type = is_native_pdf(doc)

        # Info sur les pages
        pages_info = []
        for i, page in enumerate(doc):
            pages_info.append({
                "page": i + 1,
                "width": page.rect.width,
                "height": page.rect.height,
                "has_text": len(page.get_text().strip()) > 50,
                "has_images": len(page.get_images()) > 0
            })

        doc.close()

        return jsonify({
            "success": True,
            "metadata": {
                "title": metadata.get("title", ""),
                "author": metadata.get("author", ""),
                "subject": metadata.get("subject", ""),
                "creator": metadata.get("creator", ""),
                "producer": metadata.get("producer", ""),
                "creation_date": metadata.get("creationDate", ""),
                "modification_date": metadata.get("modDate", "")
            },
            "pages_count": len(pages_info),
            "pages": pages_info,
            "pdf_type": pdf_type,
            "native_score": round(native_score, 2),
            "needs_ocr": pdf_type == "scanned"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


def parse_pages(pages_param, total_pages):
    """Parse le paramètre pages (ex: '1,2,5' ou '1-5' ou '1,3-5')"""
    pages = set()
    parts = pages_param.split(",")

    for part in parts:
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            start = max(0, int(start) - 1)
            end = min(total_pages, int(end))
            pages.update(range(start, end))
        else:
            page_num = int(part) - 1
            if 0 <= page_num < total_pages:
                pages.add(page_num)

    return sorted(pages)


def extract_text(doc, pages):
    """Extrait le texte brut des pages spécifiées"""
    texts = []
    for page_num in pages:
        page = doc[page_num]
        texts.append(page.get_text())

    return {"text": "\n\n".join(texts)}


def extract_blocks(doc, pages):
    """Extrait le texte avec informations de position par bloc"""
    all_text = []
    pages_detail = []

    for page_num in pages:
        page = doc[page_num]
        blocks = page.get_text("blocks")

        page_blocks = []
        page_text = []

        for block in blocks:
            if block[6] == 0:  # Type texte (pas image)
                text = block[4].strip()
                if text:
                    page_text.append(text)
                    page_blocks.append({
                        "text": text,
                        "bbox": {
                            "x0": block[0],
                            "y0": block[1],
                            "x1": block[2],
                            "y1": block[3]
                        }
                    })

        all_text.append("\n".join(page_text))
        pages_detail.append({
            "page": page_num + 1,
            "blocks": page_blocks
        })

    return {
        "text": "\n\n".join(all_text),
        "pages_detail": pages_detail
    }


def render_page_to_png(doc, page_num, dpi=200):
    """Rend une page PDF en bytes PNG."""
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    del pix
    return png_bytes


def ocr_single_page(png_bytes, page_num, total_pages):
    """Envoie une image PNG a PaddleOCR et retourne le resultat."""
    try:
        resp = http_requests.post(
            f"{PADDLEOCR_URL}/ocr-markdown",
            files={"image": (f"page_{page_num + 1}.png", png_bytes, "image/png")},
            timeout=OCR_PAGE_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            return data
        print(f"  PaddleOCR page {page_num + 1}: echec - {data.get('error', 'unknown')}")
    except Exception as e:
        print(f"  PaddleOCR page {page_num + 1}/{total_pages}: erreur - {e}")
    return None


def detect_structure_heuristics(text):
    """Detecte les titres dans le texte OCR et ajoute les marqueurs markdown."""
    lines = text.split('\n')
    result = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue

        if (len(stripped) < 80 and len(stripped) > 3 and stripped.isupper() and
                re.search(r'[A-Z\u00c0-\u00dc]', stripped)):
            result.append(f"# {stripped}")
            continue

        if re.match(r'^(I{1,3}|IV|V|VI{1,3}|IX|X|XI{1,3}|XIV|XV)[.\s\-\u2013]', stripped):
            result.append(f"## {stripped}")
            continue

        if re.match(r'^Article\s+\d+', stripped, re.IGNORECASE):
            result.append(f"## {stripped}")
            continue

        if re.match(r'^\d{1,2}[.\/]\s+[A-Z\u00c0-\u00dc]', stripped) and len(stripped) < 100:
            result.append(f"## {stripped}")
            continue

        result.append(line)

    return '\n'.join(result)


@app.route("/ocr-scanned-pdf", methods=["POST"])
def ocr_scanned_pdf():
    """
    OCR un PDF scanne page par page.
    Rend chaque page en image via PyMuPDF, puis envoie a PaddleOCR.
    Merge les resultats de toutes les pages.

    Retourne le meme format que PaddleOCR /ocr-markdown pour compatibilite n8n.
    """
    try:
        pdf_bytes = get_pdf_from_request()
        if not pdf_bytes:
            return jsonify({"success": False, "error": "Aucun fichier PDF fourni"}), 400

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)

        # Limiter le nombre de pages pour eviter les timeouts infra
        pages_to_process = min(page_count, MAX_OCR_PAGES)
        truncated = page_count > MAX_OCR_PAGES

        # DPI adaptatif
        dpi = 150 if pages_to_process > LARGE_PDF_THRESHOLD else 200
        if truncated:
            print(f"OCR scanned PDF: {page_count} pages (tronque a {pages_to_process}), DPI={dpi}")
        else:
            print(f"OCR scanned PDF: {page_count} pages, DPI={dpi}")

        all_lines = []
        total_confidence = 0
        pages_processed = 0
        pages_failed = 0

        start_time = time.time()

        for page_num in range(pages_to_process):
            print(f"  Rendering page {page_num + 1}/{page_count}...")
            png_bytes = render_page_to_png(doc, page_num, dpi=dpi)

            result = ocr_single_page(png_bytes, page_num, page_count)
            del png_bytes

            if result:
                page_text = result.get("markdown") or result.get("text", "")
                if page_text:
                    all_lines.append(page_text)
                total_confidence += result.get("confidence", 0)
                pages_processed += 1
            else:
                pages_failed += 1

            # Liberation memoire entre batches
            if (page_num + 1) % OCR_BATCH_SIZE == 0:
                gc.collect()
                print(f"  Batch {(page_num + 1) // OCR_BATCH_SIZE} termine")

        doc.close()
        gc.collect()

        elapsed = time.time() - start_time
        print(f"OCR termine: {pages_processed}/{pages_to_process} pages en {elapsed:.1f}s")

        # Merge des resultats
        raw_text = "\n".join(all_lines)
        markdown_text = detect_structure_heuristics(raw_text)
        avg_confidence = (total_confidence / pages_processed) if pages_processed else 0

        h1_count = len(re.findall(r'^# ', markdown_text, re.MULTILINE))
        h2_count = len(re.findall(r'^## ', markdown_text, re.MULTILINE))
        h3_count = len(re.findall(r'^### ', markdown_text, re.MULTILINE))
        has_structure = (h1_count + h2_count + h3_count) > 0

        result = {
            "success": True,
            "markdown": markdown_text,
            "text": raw_text,
            "source": "pymupdf+paddleocr",
            "has_structure": has_structure,
            "confidence": round(avg_confidence, 2),
            "lines_count": len(all_lines),
            "page_count": page_count,
            "pages_processed": pages_processed,
            "pages_failed": pages_failed,
            "is_pdf": True,
            "processing_time_ms": int(elapsed * 1000),
            "structure_stats": {
                "h1_count": h1_count,
                "h2_count": h2_count,
                "h3_count": h3_count
            }
        }

        if truncated:
            result["truncated"] = True
            result["truncated_message"] = (
                f"PDF tronque: {pages_to_process}/{page_count} pages traitees "
                f"(limite: {MAX_OCR_PAGES}). Les premieres pages contiennent "
                f"generalement les informations essentielles."
            )

        return jsonify(result)

    except Exception as e:
        print(f"OCR scanned PDF error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
