/* Card boundary detector adapted from the backend PDF crop v8 geometry. */
(function (root) {
  'use strict';
  const cfg = { minArea:.05,maxArea:.70,minAspect:1.35,maxAspect:1.85,
    edge:.60,strong:.50,strongCount:3,faintEdge:.30,faintStrong:.25,faintCount:2,
    third:.15,minRect:.70,minScore:.50,border:.03 };
  const val=(m,x,y)=>m.data[y*m.cols+x];
  const contours=(cv, mat, mode=cv.RETR_EXTERNAL)=>{
    const v=new cv.MatVector(), hierarchy=new cv.Mat(), out=[];
    try { cv.findContours(mat,v,hierarchy,mode,cv.CHAIN_APPROX_SIMPLE);
      for(let i=0;i<v.size();i++){const c=v.get(i);try {out.push({box:cv.boundingRect(c),area:cv.contourArea(c), perimeter:cv.arcLength(c,true), points:c.clone()});} finally {c.delete();}}
    } finally {v.delete();hierarchy.delete();} return out;
  };
  const rects=(cv,src,kw,kh)=>{
    const kernel=cv.getStructuringElement(cv.MORPH_RECT,new cv.Size(Math.max(1,kw),Math.max(1,kh)));
    const dst=new cv.Mat(); try {cv.morphologyEx(src,dst,cv.MORPH_OPEN,kernel);const cs=contours(cv,dst);return cs.map(c=>{c.points.delete();return c.box;});}finally{kernel.delete();dst.delete();}
  };
  const close=(cv,src,size)=>{const dst=new cv.Mat(), k=cv.getStructuringElement(cv.MORPH_RECT,new cv.Size(size,size));try{cv.morphologyEx(src,dst,cv.MORPH_CLOSE,k,new cv.Point(-1,-1),2);return dst;}finally{k.delete();}};
  function hsupport(m,y,x0,x1,band){let hits=0;const lo=Math.max(0,y-band),hi=Math.min(m.rows,y+band+1);for(let x=Math.max(0,x0);x<Math.min(m.cols,x1);x++){for(let yy=lo;yy<hi;yy++)if(val(m,x,yy)){hits++;break;}}return hits/Math.max(1,Math.min(m.cols,x1)-Math.max(0,x0));}
  function vsupport(m,x,y0,y1,band){let hits=0;const lo=Math.max(0,x-band),hi=Math.min(m.cols,x+band+1);for(let y=Math.max(0,y0);y<Math.min(m.rows,y1);y++){for(let xx=lo;xx<hi;xx++)if(val(m,xx,y)){hits++;break;}}return hits/Math.max(1,Math.min(m.rows,y1)-Math.max(0,y0));}
  function sides(m,b,band){return [hsupport(m,b.y,b.x,b.x+b.width,band),hsupport(m,b.y+b.height-1,b.x,b.x+b.width,band),vsupport(m,b.x,b.y,b.y+b.height,band),vsupport(m,b.x+b.width-1,b.y,b.y+b.height,band)];}
  function formEvidence(cv,image){
    const scale=Math.min(1,1200/Math.max(image.rows,image.cols));
    const reduced=new cv.Mat(), gray=new cv.Mat(), binary=new cv.Mat();
    try {
      if(scale<1)cv.resize(image,reduced,new cv.Size(Math.round(image.cols*scale),Math.round(image.rows*scale)),0,0,cv.INTER_AREA);
      else image.copyTo(reduced);
      cv.cvtColor(reduced,gray,cv.COLOR_RGBA2GRAY);
      cv.threshold(gray,binary,0,255,cv.THRESH_BINARY_INV+cv.THRESH_OTSU);
      const W=binary.cols,H=binary.rows;
      const hs=rects(cv,binary,Math.max(15,Math.round(W*.20)),1).filter(b=>b.width>=.65*W);
      const walls=rects(cv,binary,1,Math.max(15,Math.round(H*.20))).filter(b=>b.height>=.30*H).sort((a,b)=>a.x-b.x);
      for(let i=0;i<walls.length;i++) for(let j=i+1;j<walls.length;j++){
        const a=walls[i],b=walls[j],top=Math.max(a.y,b.y),bottom=Math.min(a.y+a.height,b.y+b.height);
        if(b.x-a.x<.65*W||bottom-top<.30*H)continue;
        const ys=hs.filter(q=>q.y>=top-5&&q.y<=bottom+5&&q.x<=a.x+W*.06&&q.x+q.width>=b.x-W*.06).map(q=>q.y).sort((a,b)=>a-b);
        const unique=[];for(const y of ys)if(!unique.length||y-unique[unique.length-1]>Math.max(4,Math.round(H*.01)))unique.push(y);
        return unique.length>=4;
      } return false;
    } finally {reduced.delete();gray.delete();binary.delete();}
  }
  function structureReject(edges,b){
    const W=edges.cols,H=edges.rows, band=Math.max(2,Math.round(Math.min(W,H)*.004)),reach=Math.max(12,Math.round(b.height*.18));
    const ext=[b.x,b.x+b.width-1].map(x=>[vsupport(edges,x,b.y-reach,b.y-band,band),vsupport(edges,x,b.y+b.height+band,b.y+b.height+reach,band)]);
    const n=ext.flat().filter(v=>v>=.48).length, s=sides(edges,b,band);
    return (ext.every(v=>Math.max(...v)>=.48)&&s[0]>=.55&&s[1]>=.55)||n>=2||
      (b.width*b.height/(W*H)>=.20&&ext.flat().some(v=>v>=.85));
  }
  function tableReject(cv,binary,b){
    if(b.width<35||b.height<25)return false;
    const roi=binary.roi(new cv.Rect(b.x,b.y,b.width,b.height));
    const hor=new cv.Mat(),ver=new cv.Mat();
    const hk=cv.getStructuringElement(cv.MORPH_RECT,new cv.Size(Math.max(12,Math.round(b.width*.28)),1));
    const vk=cv.getStructuringElement(cv.MORPH_RECT,new cv.Size(1,Math.max(12,Math.round(b.height*.28))));
    try {
      cv.morphologyEx(roi,hor,cv.MORPH_OPEN,hk);
      cv.morphologyEx(roi,ver,cv.MORPH_OPEN,vk);
      function bands(mask,horizontal,margin){
        const size=horizontal?mask.rows:mask.cols, length=horizontal?mask.cols:mask.rows,groups=[];
        for(let i=0;i<size;i++){
          if(i<=margin||i>=size-margin)continue;
          let count=0;for(let j=0;j<length;j++)if(horizontal?val(mask,j,i):val(mask,i,j))count++;
          if(count<length*.28)continue;
          const group=groups[groups.length-1];
          if(!group||i-group[group.length-1]>3)groups.push([i]);else group.push(i);
        }
        return groups.map(g=>{const mid=(g.length-1)/2;return Math.round((g[Math.floor(mid)]+g[Math.ceil(mid)])/2);});
      }
      const ys=bands(hor,true,Math.max(3,Math.round(b.height*.04)));
      const xs=bands(ver,false,Math.max(3,Math.round(b.width*.04)));
      let intersections=0;
      for(const y of ys)for(const x of xs){
        let hasH=false,hasV=false;
        for(let yy=Math.max(0,y-3);yy<Math.min(b.height,y+4);yy++)for(let xx=Math.max(0,x-3);xx<Math.min(b.width,x+4);xx++){
          hasH ||= Boolean(val(hor,xx,yy));hasV ||= Boolean(val(ver,xx,yy));
        }
        if(hasH&&hasV)intersections++;
      }
      return (ys.length>=3&&xs.length>=2&&intersections>=6)||(ys.length>=5&&xs.length>=1&&intersections>=4);
    } finally{roi.delete();hor.delete();ver.delete();hk.delete();vk.delete();}
  }
  function gridReject(lines,b,W,H){
    const ex=Math.round(b.width*.8),ey=Math.round(b.height*.8),x0=Math.max(0,b.x-ex),x1=Math.min(W,b.x+b.width+ex),y0=Math.max(0,b.y-ey),y1=Math.min(H,b.y+b.height+ey);
    const hs=lines.hs.filter(q=>q.width>=Math.max(30,b.width*.35)&&q.width>4*q.height&&q.y+Math.floor(q.height/2)>=y0&&q.y+Math.floor(q.height/2)<=y1&&q.x+q.width>=x0&&q.x<=x1);
    const vs=lines.vs.filter(q=>q.height>=Math.max(30,b.height*.35)&&q.height>4*q.width&&q.x+Math.floor(q.width/2)>=x0&&q.x+Math.floor(q.width/2)<=x1&&q.y+q.height>=y0&&q.y<=y1);
    const points=[];for(const h of hs)for(const v of vs){const x=v.x+Math.floor(v.width/2),y=h.y+Math.floor(h.height/2);if(h.x-3<=x&&x<=h.x+h.width+3&&v.y-3<=y&&y<=v.y+v.height+3)points.push([x,y]);}
    if(points.length<6||new Set(points.map(p=>Math.round(p[0]/6))).size<2||new Set(points.map(p=>Math.round(p[1]/6))).size<3)return false;
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    const overlap=Math.max(0,Math.min(b.x+b.width,Math.max(...xs))-Math.max(b.x,Math.min(...xs)))*Math.max(0,Math.min(b.y+b.height,Math.max(...ys))-Math.max(b.y,Math.min(...ys)))/(b.width*b.height);
    return overlap>=.12;
  }
  function houghBoxes(cv,edges){
    const H=edges.rows,W=edges.cols,min=Math.max(15,Math.round(Math.min(H,W)*.06)),lines=new cv.Mat(),hs=[],vs=[];
    try{cv.HoughLinesP(edges,lines,1,Math.PI/180,35,min,Math.max(5,Math.round(Math.min(H,W)*.025)));for(let i=0;i<lines.data32S.length;i+=4){const [x1,y1,x2,y2]=Array.from(lines.data32S.slice(i,i+4)),dx=x2-x1,dy=y2-y1;if(Math.abs(dy)<=Math.max(3,Math.abs(dx)*.09)&&Math.abs(dx)>=min)hs.push([Math.round((y1+y2)/2),Math.min(x1,x2),Math.max(x1,x2)]);else if(Math.abs(dx)<=Math.max(3,Math.abs(dy)*.09)&&Math.abs(dy)>=min)vs.push([Math.round((x1+x2)/2),Math.min(y1,y2),Math.max(y1,y2)]);}}finally{lines.delete();}
    function cluster(input,tol){const groups=[];input.sort((a,b)=>a[0]-b[0]);for(const p of input){const g=groups[groups.length-1];if(g&&Math.abs(p[0]-g[g.length-1][0])<=tol)g.push(p);else groups.push([p]);}return groups.map(g=>{const pos=g.map(q=>q[0]).sort((a,b)=>a-b),mid=(pos.length-1)/2;return [Math.round((pos[Math.floor(mid)]+pos[Math.ceil(mid)])/2),Math.min(...g.map(q=>q[1])),Math.max(...g.map(q=>q[2]))];}).sort((a,b)=>(b[2]-b[1])-(a[2]-a[1])).slice(0,16);}
    const hh=cluster(hs,Math.max(3,Math.round(H*.006))),vv=cluster(vs,Math.max(3,Math.round(W*.006))),boxes=[];
    for(let i=0;i<hh.length;i++)for(let j=i+1;j<hh.length;j++){const [top,bottom]=[hh[i][0],hh[j][0]].sort((a,b)=>a-b);if(bottom-top<15)continue;
      for(let k=0;k<vv.length;k++)for(let n=k+1;n<vv.length;n++){const [left,right]=[vv[k][0],vv[n][0]].sort((a,b)=>a-b),w=right-left,h=bottom-top;if(w<25)continue;const area=w*h/(W*H),ratio=w/h;if(area<cfg.minArea||area>cfg.maxArea||ratio<cfg.minAspect||ratio>cfg.maxAspect)continue;
        const overlap=[hh[i],hh[j]].map(q=>Math.max(0,Math.min(right,q[2])-Math.max(left,q[1]))/w).concat([vv[k],vv[n]].map(q=>Math.max(0,Math.min(bottom,q[2])-Math.max(top,q[1]))/h));
        if(overlap.filter(q=>q>=.18).length<3)continue;boxes.push({x:left,y:top,width:w,height:h});if(boxes.length>=250)return boxes;}}
    return boxes;
  }
  // Photographs can have textured backgrounds and softer borders than a PDF scan.
  // Use the original image-normalization contour approach, with card-shape and
  // edge guards to avoid treating small interior regions as a whole card.
  function detectPhoto(cv,rgba){
    const owned=[];
    const mat=()=>{const value=new cv.Mat();owned.push(value);return value;};
    const kernel=(w,h)=>{const value=cv.getStructuringElement(cv.MORPH_RECT,new cv.Size(w,h));owned.push(value);return value;};
    try {
      const height=rgba.rows,width=rgba.cols,scale=Math.min(1,900/height);
      const h=scale<1?900:height,w=scale<1?Math.max(1,Math.floor(width*scale)):width;
      const small=mat(),gray=mat(),blur=mat(),threshold=mat(),edge=mat(),sat=mat(),rgb=mat(),hsv=mat(),color=mat(),mask=mat(),temp=mat();
      if(scale<1)cv.resize(rgba,small,new cv.Size(w,h),0,0,cv.INTER_AREA);else rgba.copyTo(small);
      cv.cvtColor(small,gray,cv.COLOR_RGBA2GRAY);cv.GaussianBlur(gray,blur,new cv.Size(5,5),0);
      const k5=kernel(5,5),k3=kernel(3,3);
      cv.adaptiveThreshold(blur,threshold,255,cv.ADAPTIVE_THRESH_GAUSSIAN_C,cv.THRESH_BINARY,21,7);
      cv.bitwise_not(threshold,threshold);cv.morphologyEx(threshold,threshold,cv.MORPH_CLOSE,k5,new cv.Point(-1,-1),2);
      cv.Canny(blur,edge,40,140);cv.dilate(edge,temp,k5);cv.morphologyEx(temp,temp,cv.MORPH_CLOSE,k5,new cv.Point(-1,-1),2);
      cv.cvtColor(small,rgb,cv.COLOR_RGBA2RGB);cv.cvtColor(rgb,hsv,cv.COLOR_RGB2HSV);
      const channels=new cv.MatVector();try{cv.split(hsv,channels);const channel=channels.get(1);try{cv.threshold(channel,sat,17,255,cv.THRESH_BINARY);}finally{channel.delete();}}finally{channels.delete();}
      cv.Canny(blur,color,30,120);cv.dilate(color,color,k3);cv.bitwise_or(sat,color,mask);
      cv.morphologyEx(mask,mask,cv.MORPH_OPEN,k5);
      const closeW=Math.max(15,Math.floor(w*.045)|1),closeH=Math.max(11,Math.floor(h*.035)|1),kc=kernel(closeW,closeH);
      cv.morphologyEx(mask,mask,cv.MORPH_CLOSE,kc,new cv.Point(-1,-1),2);cv.dilate(mask,mask,kc);cv.morphologyEx(mask,mask,cv.MORPH_CLOSE,kc);
      const candidates=[];
      for(const [source,work] of [['adaptive',threshold],['canny',temp],['foreground',mask]]){
        const cs=contours(cv,work).sort((a,b)=>b.area-a.area);
        try {
          for(const c of cs.slice(0,20)){
            const areaRatio=c.area/(w*h);if(areaRatio<.06||areaRatio>.98||c.perimeter<=0)continue;
            let points=null;
            for(const epsilon of [.015,.02,.03,.04,.06]){
              const poly=new cv.Mat();try{cv.approxPolyDP(c.points,poly,epsilon*c.perimeter,true);if(poly.rows===4){points=Array.from({length:4},(_,i)=>({x:poly.data32S[2*i],y:poly.data32S[2*i+1]}));break;}}finally{poly.delete();}
            }
            if(!points){const rect=cv.minAreaRect(c.points),rectArea=rect.size.width*rect.size.height;if(rectArea<=1||c.area/rectArea<.35)continue;points=cv.RotatedRect.points(rect);}
            const sums=points.map(q=>q.x+q.y),diffs=points.map(q=>q.y-q.x);
            const ordered=[points[sums.indexOf(Math.min(...sums))],points[diffs.indexOf(Math.min(...diffs))],points[sums.indexOf(Math.max(...sums))],points[diffs.indexOf(Math.max(...diffs))]];
            const distance=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y);
            const cw=Math.max(distance(ordered[0],ordered[1]),distance(ordered[3],ordered[2]));
            const ch=Math.max(distance(ordered[0],ordered[3]),distance(ordered[1],ordered[2]));
            const aspect=cw/Math.max(1,ch),normalizedAspect=Math.max(aspect,1/aspect);
            if(normalizedAspect<1.25||normalizedAspect>1.85)continue;
            const cx=ordered.reduce((n,q)=>n+q.x,0)/4,cy=ordered.reduce((n,q)=>n+q.y,0)/4;
            const centerScore=1-Math.min(1,Math.hypot(cx-w/2,cy-h/2)/Math.max(1,Math.hypot(w/2,h/2)));
            const score=.65*Math.min(1,areaRatio/.75)+.35*centerScore;
            const expanded=ordered.map(q=>({x:Math.max(0,Math.min(w-1,cx+(q.x-cx)*1.024)),y:Math.max(0,Math.min(h-1,cy+(q.y-cy)*1.024))}));
            const x0=Math.floor(Math.min(...expanded.map(q=>q.x))),y0=Math.floor(Math.min(...expanded.map(q=>q.y))),x1=Math.ceil(Math.max(...expanded.map(q=>q.x))),y1=Math.ceil(Math.max(...expanded.map(q=>q.y)));
            const bw=x1-x0,bh=y1-y0,boxArea=bw*bh/(w*h),imageAspect=w/h,boxAspect=bw/Math.max(1,bh);
            if(bw<80||bh<80||boxArea<.03||boxAspect>8||boxAspect<.125||Math.max(boxAspect/imageAspect,imageAspect/boxAspect)>1.35)continue;
            // A full-frame card must not be reduced to a photo/text section.
            if(imageAspect>=1.25&&imageAspect<=1.85&&boxArea<.30)continue;
            const b={x:x0,y:y0,width:bw,height:bh};const evidence=sides(edge,b,Math.max(2,Math.round(Math.min(w,h)*.004)));
            if(evidence.filter(v=>v>=.5).length<2||evidence.reduce((a,b)=>a+b,0)/4<.35)continue;
            candidates.push({box:[Math.max(0,Math.round(x0*width/w)),Math.max(0,Math.round(y0*height/h)),Math.min(width,Math.round(x1*width/w)),Math.min(height,Math.round(y1*height/h))],score,source:'image_'+source});
          }
        } finally {for(const c of cs)c.points.delete();}
      }
      candidates.sort((a,b)=>b.score-a.score);return candidates[0]||null;
    } finally {for(const value of owned)value.delete();}
  }
  function detectImage(cv,rgba){
    const photo=detectPhoto(cv,rgba);if(photo)return photo;
    const result=detect(cv,rgba);
    if(result.box){const [x0,y0,x1,y1]=result.box,aspect=rgba.cols/rgba.rows,area=(x1-x0)*(y1-y0)/(rgba.cols*rgba.rows);
      // On a card-shaped image, faint internal lines can form a convincing
      // Hough rectangle even when the card already fills the whole image.
      // Do not automatically discard the surrounding content in that case.
      if(aspect>=1.25&&aspect<=1.85&&(area<.30||(result.source==='hough_faint'&&area<.75)))return {box:null,reason:'already_full_frame_or_ambiguous'};
    }
    return result;
  }
  function detect(cv,rgba){
    const garbage=[];const mat=()=>{const m=new cv.Mat();garbage.push(m);return m;};
    try {
      const H=rgba.rows,W=rgba.cols,scale=Math.min(1,1600/Math.max(H,W)),dw=Math.max(1,Math.round(W*scale)),dh=Math.max(1,Math.round(H*scale));
      const resized=mat();if(scale<1)cv.resize(rgba,resized,new cv.Size(dw,dh),0,0,cv.INTER_AREA);else rgba.copyTo(resized);
      const gray=mat(),blur=mat(),enhanced=mat(),normal=mat(),faint=mat(),struct=mat(),binary=mat();
      cv.cvtColor(resized,gray,cv.COLOR_RGBA2GRAY);cv.GaussianBlur(gray,blur,new cv.Size(5,5),0);
      const clahe=new cv.CLAHE(2,new cv.Size(8,8));try{clahe.apply(blur,enhanced);}finally{clahe.delete();}
      cv.Canny(blur,normal,35,110);cv.Canny(enhanced,faint,15,55);cv.bitwise_or(normal,faint,struct);
      cv.threshold(blur,binary,0,255,cv.THRESH_BINARY_INV+cv.THRESH_OTSU);
      if(formEvidence(cv,rgba))return {box:null,reason:'ruled_form_page'};
      const lines={hs:rects(cv,binary,Math.max(18,Math.round(dw*.035)),1),vs:rects(cv,binary,1,Math.max(18,Math.round(dh*.035)))};
      const band=Math.max(2,Math.round(Math.min(dw,dh)*.004)),candidates=[];
      function consider(b,source,edges,vertices,rect){
        const {x,y,width:w,height:h}=b;if(w<25||h<15||x<0||y<0||x+w>dw||y+h>dh)return;
        const aspect=w/h,area=w*h/(dw*dh);if(area<cfg.minArea||area>cfg.maxArea||aspect<cfg.minAspect||aspect>cfg.maxAspect)return;
        const faintPath=source!=='normal_contour',ss=sides(edges,b,band),edge=ss.reduce((a,b)=>a+b,0)/4,strong=ss.filter(v=>v>=(faintPath?cfg.faintStrong:cfg.strong)).length;
        const border=[x<=cfg.border*dw,y<=cfg.border*dh,dw-x-w<=cfg.border*dw,dh-y-h<=cfg.border*dh].filter(Boolean).length;
        if(edge<(faintPath?cfg.faintEdge:cfg.edge)||strong<(faintPath?cfg.faintCount:cfg.strongCount)||border>=3)return;
        if(!faintPath&&(rect<cfg.minRect||vertices!==4))return;
        if(faintPath&&strong===2&&ss.filter(v=>v>=cfg.third).length<3)return;
        if(structureReject(struct,b)||tableReject(cv,binary,b)||gridReject(lines,b,dw,dh))return;
        const score=.35*edge+.25*rect+.25*Math.min(1,1-Math.abs(aspect-1.58)/.5)+.15*strong/4;
        candidates.push({box:[Math.round(x*W/dw),Math.round(y*H/dh),Math.round((x+w)*W/dw),Math.round((y+h)*H/dh)],score:Math.round(score*10000)/10000,source});
      }
      for(const [source,edges] of [['normal_contour',normal],['faint_contour',faint]])for(const k of [5,9,15]){
        const closed=close(cv,edges,k);let cs=[];try{cs=contours(cv,closed,cv.RETR_LIST);for(const c of cs){try{if(c.area<=0||c.perimeter<=0)continue;const poly=mat();cv.approxPolyDP(c.points,poly,.025*c.perimeter,true);consider(c.box,source,edges,poly.rows,Math.min(1,c.area/(c.box.width*c.box.height)));poly.delete();garbage.pop();}finally{c.points.delete();}}}finally{closed.delete();}
      }
      if(!candidates.length)for(const box of houghBoxes(cv,faint))consider(box,'hough_faint',faint,4,1);
      candidates.sort((a,b)=>b.score-a.score);const best=candidates.find(c=>c.score>=cfg.minScore);
      return best?{box:best.box,score:best.score,source:best.source}:{box:null,reason:'no_candidate_passed_guards'};
    } finally {for(const m of garbage)m.delete();}
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={detect,detectImage};
  if(typeof root.importScripts==='function'){
    let cvPromise;
    function load(){if(!cvPromise)cvPromise=new Promise((resolve,reject)=>{try{root.importScripts('/opencv.js');const cv=root.cv;if(!cv)return reject(new Error('OpenCV unavailable'));if(cv.Mat)resolve({cv});else if(typeof cv.then==='function')cv.then(v=>resolve({cv:v}));else cv.onRuntimeInitialized=()=>resolve({cv});}catch(e){reject(e);}});return cvPromise;}
    root.onmessage=async event=>{const {id,width,height,pixels,sourceType}=event.data;try{const {cv}=await load();const rgba=cv.matFromArray(height,width,cv.CV_8UC4,new Uint8ClampedArray(pixels));try{root.postMessage({id,...(sourceType==='image'?detectImage(cv,rgba):detect(cv,rgba))});}finally{rgba.delete();}}catch(e){root.postMessage({id,error:String(e&&e.message||e)});}};
  }
})(typeof self==='undefined'?globalThis:self);
