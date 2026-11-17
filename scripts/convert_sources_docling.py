#!/usr/bin/env python3
"""Local PDF/image -> DoclingDocument JSON + Markdown. Supports resumable conversion."""
import argparse, hashlib, json, logging, math, os, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('HF_HOME', str(ROOT / '.cache-docling/huggingface'))
from PIL import Image, ImageOps
from ocrmac import ocrmac
from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, OcrMacOptions
from docling_core.types.doc import DoclingDocument, ContentLayer


def orient(source, output):
    """Score four rotations on a small preview; OCR the selected full-size page later."""
    im = ImageOps.exif_transpose(Image.open(source)).convert('RGB')
    preview = im.copy()
    preview.thumbnail((1500, 1500))
    # Vision auto-recognizes rotated text. Use oriented text quadrilaterals,
    # not OCR character counts, to infer the actual reading direction.
    import Vision
    import objc
    from ocrmac.ocrmac import pil2buf
    votes = {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0}
    with objc.autorelease_pool():
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLanguages_(['es-ES', 'en-US'])
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(pil2buf(preview), None)
        ok, error = handler.performRequests_error_([req], None)
        if not ok or error:
            raise RuntimeError(f'Vision orientation detection failed: {error}')
        observations = list(req.results())
        for item in observations:
            left, right = item.topLeft(), item.topRight()
            dx = (right.x - left.x) * preview.width
            dy = (right.y - left.y) * preview.height
            direction = math.degrees(math.atan2(dy, dx))
            rotation = (-round(direction / 90) * 90) % 360
            votes[rotation] += sum(c.isalpha() for c in item.text()) * float(item.confidence())
        n = len(observations)
    angle = max(votes, key=votes.get)
    scores = [[weight, rotation] for rotation, weight in votes.items()]
    im = im.rotate(angle, expand=True)
    im.thumbnail((2800, 2800))
    # 216 DPI makes Docling OCR scale=3 reproduce the prepared image resolution.
    im.save(output, dpi=(216, 216))
    return {'rotation_ccw': angle, 'orientation_scores': scores, 'preview_lines': n, 'prepared_size': im.size}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int)
    ap.add_argument('--max-pages', type=int, help='Maximum PDF pages per document (for a smoke test)')
    ap.add_argument('--only', help='Single source filename')
    args = ap.parse_args()
    if args.limit is not None and args.limit < 1:
        ap.error('--limit must be positive')
    if args.max_pages is not None and args.max_pages < 1:
        ap.error('--max-pages must be positive')
    out = ROOT / ('docling_preview' if args.max_pages else 'docling_output')
    for folder in ('documents', 'markdown', 'ocr', 'metadata', 'logs'):
        (out / folder).mkdir(parents=True, exist_ok=True)
    cache = ROOT / '.cache-docling/prepared'
    cache.mkdir(parents=True, exist_ok=True)
    opts = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
    opts.ocr_options = OcrMacOptions(lang=['es-ES', 'en-US'], force_full_page_ocr=True)
    converter = DocumentConverter(format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts), InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    files = sorted(p for p in (ROOT / 'sources').iterdir() if p.suffix.lower() in {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff'})
    if args.only:
        files = [p for p in files if p.name == args.only]
    if args.limit:
        files = files[:args.limit]
    if not files:
        ap.error('No matching PDF/image sources found')
    if len({p.stem for p in files}) != len(files):
        ap.error('Source filenames must have unique stems')
    failures = 0
    for i, source in enumerate(files, 1):
        stem = source.stem
        meta_path = out / 'metadata' / f'{stem}.json'
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if meta_path.exists():
            saved = json.loads(meta_path.read_text())
            if saved.get('orientation_method') == 'vision-quadrilateral-v2' and saved.get('source_sha256') == digest and saved.get('max_pages') == args.max_pages and saved.get('status') in {'success', 'empty'} and all((out / folder / f'{stem}.{ext}').exists() for folder, ext in [('documents', 'json'), ('markdown', 'md'), ('ocr', 'json')]):
                print(f'[{i}/{len(files)}] cached {source.name}', flush=True)
                continue
        started = time.time()
        meta = {'orientation_method': 'vision-quadrilateral-v2', 'source': str(source.relative_to(ROOT)), 'source_sha256': digest, 'ocr_languages': ['es-ES', 'en-US'], 'engine': 'Docling StandardPdfPipeline + OcrMac (Apple Vision)'}
        try:
            is_pdf = source.suffix.lower() == '.pdf'
            meta.update(max_pages=args.max_pages, input_type='pdf' if is_pdf else 'image')
            if is_pdf:
                # Preserve native PDF geometry and all pages; Docling performs full-page OCR.
                result = converter.convert(source, raises_on_error=False,
                    page_range=(1, args.max_pages or 9223372036854775807))
                raw = []
                meta['raw_ocr_available'] = False
            else:
                prepared = cache / f'{stem}.png'
                meta.update(orient(source, prepared))
                raw = ocrmac.OCR(Image.open(prepared), language_preference=['es-ES', 'en-US']).recognize()
                result = converter.convert(prepared, raises_on_error=False)
                meta['raw_ocr_available'] = True
            (out / 'ocr' / f'{stem}.json').write_text(json.dumps([{'text': t, 'confidence': float(c), 'bbox_normalized_bottom_left_xywh': b} for t,c,b in raw], ensure_ascii=False, indent=2))
            if result.document is None:
                raise RuntimeError(f'Conversion status: {result.status}; errors: {result.errors}')
            if result.status.value != 'success':
                raise RuntimeError(f'Conversion status: {result.status}; errors: {result.errors}')
            doc = result.document
            doc.name = stem
            # The converted geometry refers to the orientation-normalized image, while
            # the manifest retains both its transform and original source checksum.
            doc.save_as_json(out / 'documents' / f'{stem}.json')
            md = doc.export_to_markdown(traverse_pictures=True, included_content_layers=set(ContentLayer))
            (out / 'markdown' / f'{stem}.md').write_text(md)
            meta.update(status='success' if (raw or md.strip()) else 'empty', conversion_status=str(result.status), text_items=len(doc.texts), tables=len(doc.tables), markdown_characters=len(md), raw_ocr_lines=len(raw), warnings=[])
            if len(md.strip()) < 120:
                meta['warnings'].append('Little or no text: check original image; may be cover/photo/non-text.')
            if 'SUCCESS' not in str(result.status).upper():
                meta['warnings'].append(f'Non-success conversion status: {result.status}')
            # Validate the exported file against the actual DoclingDocument schema.
            DoclingDocument.model_validate_json((out / 'documents' / f'{stem}.json').read_text())
            print(f'[{i}/{len(files)}] {source.name}: {meta.get("rotation_ccw", 0)}deg, {len(raw)} OCR lines, {len(md)} chars, {time.time()-started:.1f}s', flush=True)
        except Exception as exc:
            failures += 1
            logging.exception('Failed: %s', source)
            meta.update(status='error', error=str(exc))
            print(f'[{i}/{len(files)}] ERROR {source.name}: {exc}', flush=True)
        meta['elapsed_seconds'] = round(time.time() - started, 2)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    rebuild_index(out)
    if failures:
        raise SystemExit(1)


def rebuild_index(out):
    rows = [json.loads(p.read_text()) for p in sorted((out/'metadata').glob('*.json'))]
    (out/'manifest.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    chunks, index = ['# Spanish phrase book — Docling extraction\n\nOCR output, not a manually corrected transcription. Cross-check generated Docling JSON and OCR records for uncertain wording; do not read images during interactive lessons.\n'], ['# 图片转换索引\n\n逐图 Docling JSON 保留版面结构；Markdown 便于读取。OCR 可能出错，不能作为已逐字核对的教材。\n\n| 来源标识 | 旋转 | 字符数 | 状态 | Markdown | Docling JSON |\n|---|---:|---:|---|---|---|\n']
    docs = []
    for row in rows:
        stem = Path(row['source']).stem
        md = out/'markdown'/f'{stem}.md'
        index.append(f'| {stem} | {row.get("rotation_ccw", "")} | {row.get("markdown_characters",0)} | {row["status"]} | [读取](markdown/{stem}.md) | [JSON](documents/{stem}.json) |\n')
        if md.exists() and row['status'] in {'success','empty'}:
            chunks.append(f'\n\n---\n\n# {stem}\n\nSource: {row["source"]}\n\n'+md.read_text())
            docs.append(DoclingDocument.model_validate_json((out/'documents'/f'{stem}.json').read_text()))
    (out/'all_pages.md').write_text(''.join(chunks))
    (out/'INDEX.md').write_text(''.join(index))
    if not docs:
        (out/'all_pages.docling.json').unlink(missing_ok=True)
    if docs:
        combined = DoclingDocument.concatenate(docs)
        combined.name = 'Spanish phrase book sources'
        combined.save_as_json(out/'all_pages.docling.json')
    print('SUMMARY',json.dumps({s:sum(r['status']==s for r in rows) for s in ('success','empty','error')},ensure_ascii=False),flush=True)

if __name__ == '__main__':
    main()
