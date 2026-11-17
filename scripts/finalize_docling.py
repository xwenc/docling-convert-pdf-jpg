"""Re-export all content layers, make audit text searchable, and validate coverage."""
import json
from pathlib import Path
from docling_core.types.doc import DoclingDocument, ContentLayer
from convert_sources_docling import ROOT, rebuild_index
out=ROOT/'docling_output'
source_files=sorted(p for p in (ROOT/'sources').iterdir() if p.suffix.lower() in {'.pdf','.jpg','.jpeg','.png','.webp','.tif','.tiff'})
metadata=sorted((out/'metadata').glob('*.json'))
raw_chunks=[]
if not source_files:
    raise SystemExit('No PDF/image sources found')
if {p.stem for p in metadata} != {p.stem for p in source_files}:
    raise SystemExit('Metadata does not match sources; run conversion first')
for path in metadata:
    row=json.loads(path.read_text()); stem=path.stem
    if row.get('orientation_method') != 'vision-quadrilateral-v2':
        raise SystemExit(f'Unsupported metadata: {path}')
    if row['status']=='error':
        continue
    doc=DoclingDocument.model_validate_json((out/'documents'/f'{stem}.json').read_text())
    md=doc.export_to_markdown(traverse_pictures=True,included_content_layers=set(ContentLayer))
    (out/'markdown'/f'{stem}.md').write_text(md)
    row['markdown_characters']=len(md)
    raw=json.loads((out/'ocr'/f'{stem}.json').read_text())
    if not raw and row.get('raw_ocr_available', True):
        row['status']='empty'
    raw_chunks.append('\n\n=== '+row['source']+' ===\n\n'+'\n'.join(x['text'] for x in raw))
    # Do not claim semantic reading order; original bounding boxes are in OCR JSON.
    (out/'ocr'/f'{stem}.txt').write_text('\n'.join(x['text'] for x in raw)+'\n')
    numbers=[]
    for x in raw:
        text=x['text'].strip(); bx,by,bw,bh=x['bbox_normalized_bottom_left_xywh']
        if text.isdigit() and len(text)<=3 and by>0.88:
            numbers.append(text)
    row['printed_page_candidates']=numbers
    row['markdown_export']='all content layers; traverse picture text'
    path.write_text(json.dumps(row,ensure_ascii=False,indent=2))
(out/'all_pages.ocr.txt').write_text('Raw OCR observations; order is engine observation order, not guaranteed reading order.\n'+''.join(raw_chunks))
rebuild_index(out)
rows=json.loads((out/'manifest.json').read_text())
report={
 'source_files':len(source_files),
 'source_images':sum(p.suffix.lower() != '.pdf' for p in source_files),
 'source_pdfs':sum(p.suffix.lower() == '.pdf' for p in source_files),
 'docling_documents':len(list((out/'documents').glob('*.json'))),
 'successful':sum(r['status']=='success' for r in rows),
 'empty':sum(r['status']=='empty' for r in rows),
 'errors':[r for r in rows if r['status']=='error'],
 'rotated_images':sum(r.get('rotation_ccw',0)!=0 for r in rows),
 'low_text_images':[{'source':r['source'],'characters':r.get('markdown_characters',0)} for r in rows if r.get('markdown_characters',0)<120],
 'total_markdown_characters':sum(r.get('markdown_characters',0) for r in rows),
 'note':'Schema validation only; no automated visual or semantic verification. OCR is not a manually verified transcription.'
}
merged_path = out/'all_pages.docling.json'
report['merged_document_pages'] = len(DoclingDocument.model_validate_json(merged_path.read_text()).pages) if merged_path.exists() else 0
(out/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
if report['errors']:
    raise SystemExit(1)
