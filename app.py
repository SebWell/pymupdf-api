"""
API d'extraction de texte PDF avec PyMuPDF
Extraction rapide de texte depuis des PDF natifs (non scannés)
"""

from flask import Flask, request, jsonify
import fitz  # PyMuPDF
import io
import base64
import os

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "PyMuPDF PDF Text Extraction API",
        "version": "1.0.0",
        "description": "Extraction rapide de texte depuis PDF natifs",
        "endpoints": {
            "/extract": "POST - Extraire le texte d'un PDF",
            "/info": "POST - Obtenir les métadonnées d'un PDF",
            "/health": "GET - Vérifier l'état du service"
        },
        "note": "Pour les PDF scannés (images), utilisez Tesseract ou PaddleOCR"
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


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
