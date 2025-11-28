# PyMuPDF PDF Text Extraction API

API REST rapide pour extraire du texte depuis des PDF natifs (non scannés).

> **Note** : Cette API extrait le texte des PDF natifs (texte sélectionnable). Pour les PDF scannés, utilisez [Tesseract OCR API](https://github.com/SebWell/tesseract-ocr-api) ou [PaddleOCR API](https://github.com/SebWell/paddleocr-api).

## Deploiement rapide sur Coolify

1. Créez un nouveau projet "Public Repository" dans Coolify
2. Entrez l'URL de ce repo
3. Coolify détectera automatiquement le Dockerfile
4. Déployez !

## Endpoints

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/` | Informations sur l'API |
| GET | `/health` | État de santé du service |
| POST | `/extract` | **Extraction de texte** |
| POST | `/info` | Métadonnées du PDF |

## Utilisation

### Extraire le texte d'un PDF

#### Avec un fichier (multipart/form-data)

```bash
curl -X POST \
  -F "file=@mon_document.pdf" \
  http://votre-url/extract
```

#### Avec un PDF en Base64 (JSON)

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"file": "BASE64_DU_PDF"}' \
  http://votre-url/extract
```

### Paramètres optionnels

| Paramètre | Description | Exemple |
|-----------|-------------|---------|
| `pages` | Pages spécifiques à extraire | `1,2,5` ou `1-5` ou `1,3-5` |
| `format` | Format de sortie | `text` (défaut) ou `blocks` |

### Exemples

```bash
# Extraire uniquement les pages 1 à 3
curl -X POST -F "file=@doc.pdf" -F "pages=1-3" http://votre-url/extract

# Extraire avec positions des blocs
curl -X POST -F "file=@doc.pdf" -F "format=blocks" http://votre-url/extract
```

## Réponse

### `/extract`

```json
{
  "success": true,
  "text": "Le texte extrait du PDF...",
  "pages_count": 10,
  "pages_extracted": 10,
  "characters_count": 5432,
  "pdf_type": "native",
  "native_score": 1.0,
  "needs_ocr": false
}
```

### `/info`

```json
{
  "success": true,
  "metadata": {
    "title": "Mon Document",
    "author": "John Doe",
    "creation_date": "D:20231201120000"
  },
  "pages_count": 10,
  "pages": [
    {
      "page": 1,
      "width": 612,
      "height": 792,
      "has_text": true,
      "has_images": false
    }
  ],
  "pdf_type": "native",
  "native_score": 1.0,
  "needs_ocr": false
}
```

## Détection PDF natif vs scanné

L'API détecte automatiquement si le PDF contient du texte extractible :

| `pdf_type` | `needs_ocr` | Description |
|------------|-------------|-------------|
| `native` | `false` | PDF avec texte sélectionnable |
| `mixed` | `false` | Mélange de pages texte et images |
| `scanned` | `true` | PDF scanné, nécessite OCR |

## Développement local

```bash
# Construire l'image
docker build -t pymupdf-api .

# Lancer le container
docker run -p 5000:5000 pymupdf-api

# Tester
curl http://localhost:5000/health
```

## Performances

- **Extraction** : quelques millisecondes par page
- **Taille image Docker** : ~150MB (très léger)
- **RAM requise** : ~256MB

## Licence

MIT
