
import sys,json,base64,zlib
from pathlib import Path
import cv2,numpy as np,fitz
sys.path.insert(0,'project_backend')
from app.processing.pdf_card_crop import DEFAULT_CONFIG,detect,ruled_form_evidence
def make(name):
 if name=='photo-converted.png':
  source=next(Path('benchmark/input').glob('*.jpg'))
  original=cv2.imdecode(np.fromfile(source,dtype=np.uint8),cv2.IMREAD_COLOR)
  ok,encoded=cv2.imencode('.png',original)
  if not ok: raise RuntimeError('Unable to create PNG fixture')
  img=cv2.imdecode(encoded,cv2.IMREAD_COLOR)
 elif name.startswith('synthetic:'):
  img=np.full((1000,800,3),255,np.uint8)
  if name in ('synthetic:card','synthetic:faint'):
   c=30 if name.endswith('card') else 230
   cv2.rectangle(img,(160,340),(640,640),(c,c,c),3)
  if name=='synthetic:portrait':
   cv2.rectangle(img,(240,260),(560,740),(30,30,30),3)
  if name=='synthetic:broken':
   for x in range(160,640,75):
    for y in (340,640): cv2.line(img,(x,y),(min(x+48,640),y),(100,100,100),3)
   for y in range(340,640,75):
    for x in (160,640): cv2.line(img,(x,y),(x,min(y+48,640)),(100,100,100),3)
  if name=='synthetic:grid':
   for y in range(100,901,160): cv2.line(img,(80,y),(720,y),(0,0,0),3)
   for x in (80,400,720): cv2.line(img,(x,100),(x,900),(0,0,0),3)
 else:
  p=next(Path('benchmark/input').glob(name))
  if p.suffix.lower()=='.pdf':
   with fitz.open(p) as doc:
    pix=doc[0].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False,colorspace=fitz.csRGB)
    img=cv2.cvtColor(np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3),cv2.COLOR_RGB2BGR)
  else: img=cv2.imdecode(np.fromfile(p,dtype=np.uint8),cv2.IMREAD_COLOR)
  h,w=img.shape[:2];sc=min(1,1600/max(h,w));img=cv2.resize(img,(round(w*sc),round(h*sc)),interpolation=cv2.INTER_AREA) if sc<1 else img
 form=ruled_form_evidence(img,DEFAULT_CONFIG)
 result=detect(img,DEFAULT_CONFIG)[0] if not form['reject'] else None
 bbox=result['bbox'] if result else None
 if bbox: bbox=[bbox[0],bbox[1],bbox[0]+bbox[2],bbox[1]+bbox[3]]
 rgba=cv2.cvtColor(img,cv2.COLOR_BGR2RGBA)
 return {'name':name,'width':rgba.shape[1],'height':rgba.shape[0],'pixels':base64.b64encode(zlib.compress(rgba.tobytes())).decode(),'bbox':bbox}
fixtures=['synthetic:card','synthetic:faint','synthetic:broken','synthetic:portrait','synthetic:blank','synthetic:grid']
fixtures += [p.name for p in Path('benchmark/input').glob('*.pdf')]
fixtures += [p.name for p in Path('benchmark/input').glob('*') if p.suffix.lower() in ('.jpg', '.png')]
if any(Path('benchmark/input').glob('*.jpg')): fixtures.append('photo-converted.png')
print(json.dumps([make(name) for name in fixtures]))
