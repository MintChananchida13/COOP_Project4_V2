"use client";

import React, { useRef, useMemo, useEffect, useState } from 'react';
import { 
  RotateCw, Crop, Check, RefreshCw, Minus, Plus, Scissors, 
  ChevronLeft, ChevronRight, Maximize2, Sparkles, FlipHorizontal, FlipVertical,
  RotateCcw
} from 'lucide-react';

interface PageConfig {
  rotation: number;
  brightness: number;
  contrast: number;
  sharpness: number;
  perspectiveV: number;
  perspectiveH: number;
  flipH: boolean;
  flipV: boolean;
  cropBox: { 
    x: number; 
    y: number; 
    width: number; 
    height: number;
    renderedWidth?: number;
    renderedHeight?: number;
  } | null;
  cropCorners: { x: number; y: number }[] | null;
  isCropActive: boolean;
  isCropped: boolean;
  croppedLocalUrl: string | null;
}

const warpPerspective = (
  srcImg: HTMLImageElement,
  corners: { x: number; y: number }[], // 4 corners in natural coordinates
  destW: number,
  destH: number
): string => {
  const destCanvas = document.createElement("canvas");
  destCanvas.width = destW;
  destCanvas.height = destH;
  const destCtx = destCanvas.getContext("2d");
  if (!destCtx) return "";

  const srcCanvas = document.createElement("canvas");
  srcCanvas.width = srcImg.naturalWidth;
  srcCanvas.height = srcImg.naturalHeight;
  const srcCtx = srcCanvas.getContext("2d");
  if (!srcCtx) return "";
  srcCtx.drawImage(srcImg, 0, 0);
  const srcData = srcCtx.getImageData(0, 0, srcCanvas.width, srcCanvas.height);
  const srcPixels = srcData.data;

  const destData = destCtx.createImageData(destW, destH);
  const destPixels = destData.data;

  const solveLinearSystem = (matrix: number[][], values: number[]) => {
    const n = values.length;
    const a = matrix.map((row, index) => [...row, values[index]]);
    for (let col = 0; col < n; col += 1) {
      let pivot = col;
      for (let row = col + 1; row < n; row += 1) {
        if (Math.abs(a[row][col]) > Math.abs(a[pivot][col])) pivot = row;
      }
      if (Math.abs(a[pivot][col]) < 1e-10) return null;
      [a[col], a[pivot]] = [a[pivot], a[col]];
      const divisor = a[col][col];
      for (let j = col; j <= n; j += 1) a[col][j] /= divisor;
      for (let row = 0; row < n; row += 1) {
        if (row === col) continue;
        const factor = a[row][col];
        for (let j = col; j <= n; j += 1) a[row][j] -= factor * a[col][j];
      }
    }
    return a.map((row) => row[n]);
  };

  const sourcePoints = [
    { u: 0, v: 0, x: corners[0].x, y: corners[0].y },
    { u: destW - 1, v: 0, x: corners[1].x, y: corners[1].y },
    { u: destW - 1, v: destH - 1, x: corners[2].x, y: corners[2].y },
    { u: 0, v: destH - 1, x: corners[3].x, y: corners[3].y },
  ];
  const matrix: number[][] = [];
  const values: number[] = [];
  sourcePoints.forEach(({ u, v, x, y }) => {
    matrix.push([u, v, 1, 0, 0, 0, -u * x, -v * x]);
    values.push(x);
    matrix.push([0, 0, 0, u, v, 1, -u * y, -v * y]);
    values.push(y);
  });
  const homography = solveLinearSystem(matrix, values);
  if (!homography) return "";
  const [h0, h1, h2, h3, h4, h5, h6, h7] = homography;

  const srcW = srcImg.naturalWidth;
  const srcH = srcImg.naturalHeight;

  for (let y = 0; y < destH; y++) {
    for (let x = 0; x < destW; x++) {
      const denom = h6 * x + h7 * y + 1;
      const srcX = Math.round((h0 * x + h1 * y + h2) / (denom || 1));
      const srcY = Math.round((h3 * x + h4 * y + h5) / (denom || 1));

      const destIdx = (y * destW + x) * 4;

      if (srcX >= 0 && srcX < srcW && srcY >= 0 && srcY < srcH) {
        const srcIdx = (srcY * srcW + srcX) * 4;
        destPixels[destIdx] = srcPixels[srcIdx];
        destPixels[destIdx + 1] = srcPixels[srcIdx + 1];
        destPixels[destIdx + 2] = srcPixels[srcIdx + 2];
        destPixels[destIdx + 3] = srcPixels[srcIdx + 3];
      } else {
        destPixels[destIdx] = 255;
        destPixels[destIdx + 1] = 255;
        destPixels[destIdx + 2] = 255;
        destPixels[destIdx + 3] = 255;
      }
    }
  }

  destCtx.putImageData(destData, 0, 0);
  return destCanvas.toDataURL("image/jpeg", 0.95);
};

const orderCropCorners = (corners: { x: number; y: number }[]) => {
  const sortedByY = [...corners].sort((a, b) => a.y - b.y);
  const top = sortedByY.slice(0, 2).sort((a, b) => a.x - b.x);
  const bottom = sortedByY.slice(2, 4).sort((a, b) => a.x - b.x);
  return [top[0], top[1], bottom[1], bottom[0]];
};

const extractAxisAlignedCropUrl = (
  imgEl: HTMLImageElement,
  corners: { x: number; y: number }[]
): string | null => {
  const naturalWidth = imgEl.naturalWidth;
  const naturalHeight = imgEl.naturalHeight;
  const minX = Math.max(0, Math.min(...corners.map((corner) => corner.x)));
  const minY = Math.max(0, Math.min(...corners.map((corner) => corner.y)));
  const maxX = Math.min(naturalWidth, Math.max(...corners.map((corner) => corner.x)));
  const maxY = Math.min(naturalHeight, Math.max(...corners.map((corner) => corner.y)));
  const width = Math.round(maxX - minX);
  const height = Math.round(maxY - minY);

  if (width < 24 || height < 24) return null;

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(imgEl, minX, minY, width, height, 0, 0, width, height);
  return canvas.toDataURL("image/jpeg", 0.95);
};

const extractPerspectiveCropAreaUrl = (imgEl: HTMLImageElement, config: PageConfig): string | null => {
  if (!imgEl || !imgEl.complete || imgEl.naturalWidth === 0 || !config.cropCorners || config.cropCorners.length !== 4) return null;

  const naturalWidth = imgEl.naturalWidth;
  const naturalHeight = imgEl.naturalHeight;
  const renderedWidth = imgEl.clientWidth || 1;
  const renderedHeight = imgEl.clientHeight || 1;

  const scaleX = naturalWidth / renderedWidth;
  const scaleY = naturalHeight / renderedHeight;

  const naturalCorners = orderCropCorners(config.cropCorners.map(c => ({
    x: c.x * scaleX,
    y: c.y * scaleY
  })));

  const dist = (p1: any, p2: any) => Math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2);
  
  const wTop = dist(naturalCorners[0], naturalCorners[1]);
  const wBottom = dist(naturalCorners[3], naturalCorners[2]);
  const hLeft = dist(naturalCorners[0], naturalCorners[3]);
  const hRight = dist(naturalCorners[1], naturalCorners[2]);

  const destW = Math.round(Math.max(wTop, wBottom));
  const destH = Math.round(Math.max(hLeft, hRight));
  const aspectRatio = destW / (destH || 1);
  const areaRatio = (destW * destH) / (naturalWidth * naturalHeight);

  if (destW < 24 || destH < 24) return extractAxisAlignedCropUrl(imgEl, naturalCorners);
  if (!Number.isFinite(aspectRatio) || aspectRatio < 0.12 || aspectRatio > 8 || areaRatio < 0.01) {
    return extractAxisAlignedCropUrl(imgEl, naturalCorners);
  }
  return warpPerspective(imgEl, naturalCorners, destW, destH);
};

const multiply2d = (
  left: { a: number; b: number; c: number; d: number },
  right: { a: number; b: number; c: number; d: number }
) => ({
  a: left.a * right.a + left.c * right.b,
  b: left.b * right.a + left.d * right.b,
  c: left.a * right.c + left.c * right.d,
  d: left.b * right.c + left.d * right.d,
});

const buildImageTransform = (config: PageConfig) => {
  const angleRad = (config.rotation * Math.PI) / 180;
  const skewX = Math.tan((config.perspectiveH * Math.PI) / 180);
  const skewY = Math.tan((config.perspectiveV * Math.PI) / 180);
  const scale = {
    a: config.flipH ? -1 : 1,
    b: 0,
    c: 0,
    d: config.flipV ? -1 : 1,
  };
  const rotation = {
    a: Math.cos(angleRad),
    b: Math.sin(angleRad),
    c: -Math.sin(angleRad),
    d: Math.cos(angleRad),
  };
  const skew = {
    a: 1,
    b: skewY,
    c: skewX,
    d: 1,
  };
  return multiply2d(scale, multiply2d(rotation, skew));
};

const transformPoint = (
  matrix: { a: number; b: number; c: number; d: number },
  x: number,
  y: number
) => ({
  x: matrix.a * x + matrix.c * y,
  y: matrix.b * x + matrix.d * y,
});

const DEFAULT_CONFIG: PageConfig = {
  rotation: 0,
  brightness: 100,
  contrast: 100,
  sharpness: 0,
  perspectiveV: 0,
  perspectiveH: 0,
  flipH: false,
  flipV: false,
  cropBox: null,
  cropCorners: null,
  isCropActive: false,
  isCropped: false,
  croppedLocalUrl: null
};

interface AdjustZoneProps {
  imagesList: string[];
  currentIndex: number;
  onIndexChange: (index: number) => void;
  pagesConfig: PageConfig[];
  setPagesConfig: React.Dispatch<React.SetStateAction<PageConfig[]>>;
  onBatchConfirm: (finalImages: string[]) => void;
}

export default function AdjustZone({
  imagesList,
  currentIndex,
  onIndexChange,
  pagesConfig,
  setPagesConfig,
  onBatchConfirm,
}: AdjustZoneProps) {
  
  // ✨ สเตตพิเศษ: ใช้ล็อกและจำ URL รูปเอกสารแรกสุดที่ component นี้เคยได้รับ (ห้ามใครเปลี่ยน)
  const [originalBackupList, setOriginalBackupList] = useState<string[]>([]);

  // ถ้าโหลดเข้ามาครั้งแรกสุด และ backup ยังว่าง ให้เซ็ตค่าจำรูปออริจินัลไว้เลย
  useEffect(() => {
    if (imagesList.length > 0 && originalBackupList.length === 0) {
      setOriginalBackupList([...imagesList]);
    }
  }, [imagesList]);

  // สลับมาดึงจากข้อมูล Backup ดั้งเดิมแทน imagesList โดยตรง เผื่อกรณีโดนเขียนทับไปแล้ว
  const currentRawUrl = originalBackupList[currentIndex] || imagesList[currentIndex] || "";
  
  const rawImageRef = useRef<HTMLImageElement | null>(null);
  const croppedImageRef = useRef<HTMLImageElement | null>(null);
  const [liveCropPreviewUrl, setLiveCropPreviewUrl] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [activeCornerIdx, setActiveCornerIdx] = useState<number | null>(null);
  const [cropError, setCropError] = useState<string | null>(null);

  const currentConfig = useMemo(() => {
    return pagesConfig[currentIndex] || { ...DEFAULT_CONFIG };
  }, [pagesConfig, currentIndex]);

  const { 
    rotation, brightness, contrast, sharpness, 
    perspectiveV, perspectiveH, flipH, flipV,
    cropBox, isCropActive, isCropped, croppedLocalUrl 
  } = currentConfig;

  const updateCurrentConfig = (fields: Partial<PageConfig>) => {
    setPagesConfig(prev => {
      const updated = [...prev];
      if (!updated[currentIndex]) {
        updated[currentIndex] = { ...DEFAULT_CONFIG, ...fields };
      } else {
        updated[currentIndex] = { ...updated[currentIndex], ...fields };
      }
      return updated;
    });
  };

  useEffect(() => {
    setLiveCropPreviewUrl(isCropped ? croppedLocalUrl : null);
    setCropError(null);
  }, [isCropped, croppedLocalUrl, currentIndex]);

  const processSingleImageCanvas = (imgEl: HTMLImageElement, config: PageConfig, baseIsCropped: boolean): string => {
    // 🛡️ ป้องกันรูปค้าง/รูปดำ: ถ้ารูปยังโหลดไม่เสร็จหรือไม่มีมิติความกว้าง ให้คืนค่า src ดั้งเดิมทันที
    if (!imgEl || !imgEl.complete || imgEl.naturalWidth === 0 || imgEl.naturalHeight === 0) {
      return imgEl?.src || "";
    }

    const naturalWidth = imgEl.naturalWidth;
    const naturalHeight = imgEl.naturalHeight;

    let sourceCanvas = document.createElement('canvas');
    let targetWidth = naturalWidth;
    let targetHeight = naturalHeight;

    if (!baseIsCropped && config.cropBox) {
      const renderedWidth = config.cropBox.renderedWidth || imgEl.clientWidth || 500;
      const renderedHeight = config.cropBox.renderedHeight || imgEl.clientHeight || 500;
      
      const safeRenderedWidth = renderedWidth <= 0 ? 500 : renderedWidth;
      const safeRenderedHeight = renderedHeight <= 0 ? 500 : renderedHeight;

      const imgRatio = naturalWidth / naturalHeight;
      const containerRatio = safeRenderedWidth / safeRenderedHeight;
      
      let displayedImgWidth = safeRenderedWidth, displayedImgHeight = safeRenderedHeight, offsetX = 0, offsetY = 0;
      if (imgRatio > containerRatio) { 
        displayedImgHeight = safeRenderedWidth / imgRatio; 
        offsetY = (safeRenderedHeight - displayedImgHeight) / 2; 
      } else { 
        displayedImgWidth = safeRenderedHeight * imgRatio; 
        offsetX = (safeRenderedWidth - displayedImgWidth) / 2; 
      }

      const scaleX = naturalWidth / (displayedImgWidth || 1);
      const scaleY = naturalHeight / (displayedImgHeight || 1);
      
      const targetX = Math.max(0, (config.cropBox.x - offsetX) * scaleX);
      const targetY = Math.max(0, (config.cropBox.y - offsetY) * scaleY);
      targetWidth = Math.min(naturalWidth, config.cropBox.width * scaleX);
      targetHeight = Math.min(naturalHeight, config.cropBox.height * scaleY);

      if (targetWidth <= 0 || targetHeight <= 0) return imgEl.src;

      sourceCanvas.width = targetWidth;
      sourceCanvas.height = targetHeight;
      const sCtx = sourceCanvas.getContext('2d');
      if (sCtx) {
        sCtx.drawImage(imgEl, targetX, targetY, targetWidth, targetHeight, 0, 0, targetWidth, targetHeight);
      }
    } else {
      sourceCanvas.width = naturalWidth;
      sourceCanvas.height = naturalHeight;
      const sCtx = sourceCanvas.getContext('2d');
      if (sCtx) {
        sCtx.drawImage(imgEl, 0, 0);
      }
    }

    const finalCanvas = document.createElement('canvas');
    const ctx = finalCanvas.getContext('2d');
    if (!ctx) return imgEl.src;

    const matrix = buildImageTransform(config);
    const halfW = targetWidth / 2;
    const halfH = targetHeight / 2;
    const transformedCorners = [
      transformPoint(matrix, -halfW, -halfH),
      transformPoint(matrix, halfW, -halfH),
      transformPoint(matrix, halfW, halfH),
      transformPoint(matrix, -halfW, halfH),
    ];
    const minX = Math.min(...transformedCorners.map((point) => point.x));
    const minY = Math.min(...transformedCorners.map((point) => point.y));
    const maxX = Math.max(...transformedCorners.map((point) => point.x));
    const maxY = Math.max(...transformedCorners.map((point) => point.y));
    const finalWidth = Math.ceil(maxX - minX);
    const finalHeight = Math.ceil(maxY - minY);

    if (finalWidth <= 0 || finalHeight <= 0 || !Number.isFinite(finalWidth) || !Number.isFinite(finalHeight)) return imgEl.src;

    finalCanvas.width = finalWidth;
    finalCanvas.height = finalHeight;

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, finalWidth, finalHeight);

    ctx.filter = `brightness(${config.brightness}%) contrast(${config.contrast + config.sharpness}%)`;
    ctx.setTransform(matrix.a, matrix.b, matrix.c, matrix.d, -minX, -minY);
    ctx.drawImage(sourceCanvas, -halfW, -halfH);

    return finalCanvas.toDataURL('image/jpeg', 0.95);
  };

  // ✨ ฟังก์ชันสลับหน้าอัจฉริยะ: เรนเดอร์และเซฟงานหน้าปัจจุบันลงหน่วยความจำแยกหน้าก่อนเปิดหน้าถัดไป
  const handleSafeIndexChange = async (nextIndex: number) => {
    if (isProcessing) return;
    setIsProcessing(true);

    try {
      const activeImageElement = isCropped ? croppedImageRef.current : rawImageRef.current;
      
      // 🛡️ เช็คสถานะเอกสารบน DOM ก่อนการเซฟลงประวัติเพื่อบล็อกการสร้างรูปเอกสารดำเปล่า ๆ
      if (activeImageElement && activeImageElement.complete && activeImageElement.naturalWidth > 0) {
        const config = pagesConfig[currentIndex] || { ...DEFAULT_CONFIG };
        const resultUrl = processSingleImageCanvas(activeImageElement, config, isCropped);
        
        if (resultUrl && !resultUrl.startsWith("data:;")) {
          setPagesConfig(prev => {
            const updated = [...prev];
            if (!updated[currentIndex]) updated[currentIndex] = { ...DEFAULT_CONFIG };
            updated[currentIndex] = { 
              ...updated[currentIndex], 
              croppedLocalUrl: resultUrl 
            };
            return updated;
          });
        }
      }
    } catch (err) {
      console.error("Error auto-saving page changes:", err);
    } finally {
      setIsProcessing(false);
      onIndexChange(nextIndex); // เปลี่ยนหน้า
    }
  };

  // 🚀 รวบรวมเอกสารตกแต่งสมบูรณ์ของทุกหน้าแยกอิสระ ส่งขึ้นสู่หน้าประมวลผลกล่องข้อความใหญ่
  const handleConfirmAll = async () => {
    if (isProcessing) return;
    setIsProcessing(true);

    try {
      let finalProcessedImages = originalBackupList.length > 0 ? [...originalBackupList] : [...imagesList];
      const activeImageElement = isCropped ? croppedImageRef.current : rawImageRef.current;

      let currentResultUrl = "";
      if (activeImageElement && activeImageElement.complete && activeImageElement.naturalWidth > 0) {
        const config = pagesConfig[currentIndex] || { ...DEFAULT_CONFIG };
        currentResultUrl = processSingleImageCanvas(activeImageElement, config, isCropped);
      }

      // วนรอบตรวจสอบเพื่อรวบรวมข้อมูล Base64 ที่ดีที่สุดของแต่ละหน้าส่งออก
      const allPagesFinalArray = finalProcessedImages.map((rawUrl, idx) => {
        if (idx === currentIndex && currentResultUrl && !currentResultUrl.startsWith("data:;")) {
          return currentResultUrl;
        }
        return pagesConfig[idx]?.croppedLocalUrl || rawUrl;
      });

      onBatchConfirm(allPagesFinalArray);

    } catch (error) {
      console.error("Error processing final images:", error);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleResetToDefault = () => {
    setLiveCropPreviewUrl(null);
    setCropError(null);
    
    updateCurrentConfig({ 
      ...DEFAULT_CONFIG,
      isCropped: false,
      isCropActive: false,
      croppedLocalUrl: null,
      cropBox: null,
      cropCorners: null
    });

    setPagesConfig(prev => {
      const updated = [...prev];
      if (updated[currentIndex]) {
        updated[currentIndex] = {
          ...DEFAULT_CONFIG,
          croppedLocalUrl: null
        };
      }
      return updated;
    });
  };

  const handleCornerMouseDown = (e: React.MouseEvent, index: number) => {
    e.preventDefault();
    e.stopPropagation();
    setActiveCornerIdx(index);
  };

  const handleContainerMouseMove = (e: React.MouseEvent) => {
    if (activeCornerIdx === null || !rawImageRef.current) return;
    const rect = rawImageRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
    const y = Math.max(0, Math.min(rect.height, e.clientY - rect.top));

    if (currentConfig.cropCorners) {
      const updatedCorners = currentConfig.cropCorners.map((c, idx) => 
        idx === activeCornerIdx ? { x, y } : c
      );
      updateCurrentConfig({ cropCorners: updatedCorners });
    }
  };

  const handleContainerMouseUp = () => {
    setActiveCornerIdx(null);
  };

  const handleInstantLocalCrop = () => {
    setCropError(null);
    if (currentConfig.cropCorners && currentConfig.cropCorners.length === 4 && rawImageRef.current && rawImageRef.current.complete && rawImageRef.current.naturalWidth > 0) {
      const croppedUrl = extractPerspectiveCropAreaUrl(rawImageRef.current, currentConfig);
      if (!croppedUrl) {
        setCropError("ไม่สามารถ Crop เอกสารจากกรอบนี้ได้ กรุณาปรับมุมทั้ง 4 จุดให้ครอบคลุมเอกสารอีกครั้ง");
        return;
      }
      updateCurrentConfig({ 
        isCropped: true, 
        isCropActive: false,
        croppedLocalUrl: croppedUrl
      });
      return;
    }
    setCropError("รูปเอกสารยังไม่พร้อมสำหรับการ Crop กรุณารอสักครู่แล้วลองอีกครั้ง");
  };

  const handleModifyCrop = () => {
    setCropError(null);
    updateCurrentConfig({ isCropped: false, isCropActive: true });
  };

  const handleCancelCrop = () => {
    setLiveCropPreviewUrl(null);
    setCropError(null);
    updateCurrentConfig({
      isCropped: false,
      isCropActive: false,
      croppedLocalUrl: null,
      cropCorners: null,
      cropBox: null,
    });
  };

  const handleActivateCrop = () => {
    setCropError(null);
    const imgEl = rawImageRef.current;
    if (!imgEl) return;
    
    if (currentConfig.cropCorners) {
      updateCurrentConfig({ isCropActive: true, isCropped: false });
      return;
    }

    const w = imgEl.clientWidth || 300;
    const h = imgEl.clientHeight || 400;
    const marginW = w * 0.1;
    const marginH = h * 0.1;
    updateCurrentConfig({
      isCropActive: true,
      isCropped: false,
      cropCorners: [
        { x: marginW, y: marginH },
        { x: w - marginW, y: marginH },
        { x: w - marginW, y: h - marginH },
        { x: marginW, y: h - marginH }
      ]
    });
  };

  const handleNumberChange = (val: string, min: number, max: number, key: keyof PageConfig) => {
    let num = Number(val);
    if (num > max) num = max;
    if (num < min) num = min;
    updateCurrentConfig({ [key]: num });
  };

  const handleHandleStyle = {
    width: "10px", height: "10px", background: "#ffffff",
    border: "2px solid #0052cc", borderRadius: "50%",
    boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
  };

  const dynamicPreviewStyle = useMemo(() => {
    let transforms = [];
    if (flipH) transforms.push("scaleX(-1)");
    if (flipV) transforms.push("scaleY(-1)");
    if (rotation !== 0) transforms.push(`rotate(${rotation}deg)`);
    
    if (perspectiveH !== 0) transforms.push(`skewX(${perspectiveH}deg)`);
    if (perspectiveV !== 0) transforms.push(`skewY(${perspectiveV}deg)`);

    return {
      transform: transforms.join(" "),
      transformOrigin: "center center",
      filter: `brightness(${brightness}%) contrast(${contrast + sharpness}%)`,
      transition: "transform 0.15s ease-out, filter 0.1s ease"
    };
  }, [rotation, perspectiveV, perspectiveH, flipH, flipV, brightness, contrast, sharpness]);

return (
  <div className="max-w-7xl mx-auto rounded-2xl border border-slate-200 bg-[#f8fafc] p-4 md:p-6">
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-12 xl:items-stretch">
      {/* Left Preview */}
      <div className="flex flex-col gap-4 xl:col-span-8 xl:h-[calc(100vh-140px)] xl:min-h-[720px]">
        <div className="relative flex min-h-[520px] flex-1 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-[#edf2f7] p-6 shadow-inner sm:min-h-[640px] xl:min-h-0">
          <div className="relative flex items-center justify-center w-full h-full">
            {isCropped && liveCropPreviewUrl ? (
              <img
                ref={croppedImageRef}
                src={liveCropPreviewUrl}
                alt="Cropped Preview"
                className="max-h-[380px] md:max-h-[480px] max-w-full w-auto h-auto block border border-slate-300 shadow-2xl bg-white rounded-lg select-none object-contain"
                style={dynamicPreviewStyle}
              />
            ) : (
              <div className="relative inline-block max-h-[380px] md:max-h-[480px] max-w-full">
                <img
                  ref={rawImageRef}
                  src={currentRawUrl}
                  alt="Document Preview"
                  className="max-h-[380px] md:max-h-[480px] max-w-full block border border-slate-200 shadow-xl bg-white rounded-lg select-none object-contain"
                  style={isCropActive ? undefined : dynamicPreviewStyle}
                />

                {isCropActive &&
                  currentConfig.cropCorners &&
                  currentConfig.cropCorners.length === 4 && (
                    <div
                      className="absolute inset-0 w-full h-full pointer-events-auto"
                      onMouseMove={handleContainerMouseMove}
                      onMouseUp={handleContainerMouseUp}
                      onMouseLeave={handleContainerMouseUp}
                    >
                      <svg className="absolute inset-0 w-full h-full pointer-events-none z-30">
                        <polygon
                          points={currentConfig.cropCorners
                            .map((c) => `${c.x},${c.y}`)
                            .join(" ")}
                          fill="rgba(59, 130, 246, 0.18)"
                          stroke="#3b82f6"
                          strokeWidth="2.5"
                          strokeDasharray="4,4"
                        />
                      </svg>

                      {currentConfig.cropCorners.map((c, idx) => (
                        <div
                          key={idx}
                          onMouseDown={(e) => handleCornerMouseDown(e, idx)}
                          style={{
                            left: `${c.x}px`,
                            top: `${c.y}px`,
                            transform: "translate(-50%, -50%)",
                          }}
                          className="absolute w-6 h-6 bg-white border-2 border-blue-600 rounded-full shadow-lg cursor-move z-40 flex items-center justify-center hover:scale-110 hover:bg-blue-50 transition-all select-none"
                        >
                          <span className="text-[10.5px] font-extrabold text-blue-600">
                            {idx + 1}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
              </div>
            )}
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-center justify-between gap-4 rounded-xl border border-slate-200 bg-[#edf2f7] p-3 sm:flex-row">
          <div className="text-slate-600 text-xs font-semibold px-2 shrink-0">
            เอกสาร :
            <span className="text-blue-600 font-mono font-bold ml-1">
              {currentIndex + 1} / {imagesList.length} หน้า
            </span>
          </div>

          <div className="flex items-center gap-2 flex-1 justify-center w-full">
            <button
              type="button"
              disabled={currentIndex === 0 || isProcessing}
              onClick={() => handleSafeIndexChange(currentIndex - 1)}
              className="p-1.5 rounded-lg bg-white border border-slate-200 text-slate-500 hover:text-slate-700 disabled:opacity-25"
            >
              <ChevronLeft size={16} />
            </button>

            <div
              className="flex gap-2 overflow-x-auto max-w-xl py-1 no-scrollbar"
              style={{ msOverflowStyle: "none", scrollbarWidth: "none" }}
            >
              <style>{`.no-scrollbar::-webkit-scrollbar { display: none; }`}</style>

              {imagesList.map((url, idx) => (
                <button
                  key={idx}
                  type="button"
                  disabled={isProcessing}
                  onClick={() => handleSafeIndexChange(idx)}
                  className={`relative w-11 h-14 rounded border-2 overflow-hidden bg-white shrink-0 transition-all ${
                    idx === currentIndex
                      ? "border-blue-500 ring-2 ring-blue-500/10 scale-105"
                      : "border-slate-200 opacity-50 hover:opacity-100"
                  }`}
                >
                  <img
                    src={pagesConfig[idx]?.croppedLocalUrl || originalBackupList[idx] || url}
                    className="w-full h-full object-cover"
                    alt=""
                  />
                  <div className="absolute bottom-0 inset-x-0 bg-slate-900/80 text-[8px] text-slate-300 text-center font-mono py-0.5">
                    #{idx + 1}
                  </div>
                </button>
              ))}
            </div>

            <button
              type="button"
              disabled={currentIndex === imagesList.length - 1 || isProcessing}
              onClick={() => handleSafeIndexChange(currentIndex + 1)}
              className="p-1.5 rounded-lg bg-white border border-slate-200 text-slate-500 hover:text-slate-700 disabled:opacity-25"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      </div>

{/* Right Tools */}
<div className="flex flex-col xl:col-span-4 xl:h-[calc(100vh-140px)] xl:min-h-[720px]">
  <div className="min-h-0 flex-1 overflow-y-auto rounded-t-xl border border-slate-200 bg-white p-4 shadow-sm space-y-4">
    <div className="rounded-xl border border-rose-100 bg-rose-50/40 p-3">
      <h3 className="text-xs font-bold text-rose-700 uppercase tracking-wider flex items-center gap-1.5">
        <RotateCcw size={13} /> คืนค่าการปรับแต่งทั้งหมด
      </h3>

      <p className="mt-1 text-[11px] text-slate-400">
        ล้างฟิลเตอร์ มุมเอียง และกรอบครอป
      </p>

      <button
        type="button"
        onClick={handleResetToDefault}
        className="mt-3 w-full py-2 bg-white border border-rose-200 text-rose-600 hover:bg-rose-50 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5"
      >
        <RefreshCw size={13} /> กลับไปใช้เอกสารต้นฉบับ
      </button>
    </div>

    <div className="border-t border-slate-100 pt-4">
      <div className="flex justify-between items-center">
        <h3 className="text-xs font-bold text-[#172b4d] uppercase tracking-wider flex items-center gap-1.5">
          <Crop size={13} className="text-blue-600" /> Crop เอกสาร
        </h3>

        <span
          className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded ${
            isCropped
              ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
              : "bg-slate-50 text-slate-400"
          }`}
        >
          {isCropped ? "Crop สำเร็จ" : ""}
        </span>
      </div>

      <div className="mt-3">
        {!isCropped ? (
          isCropActive ? (
            <button
              type="button"
              onClick={handleInstantLocalCrop}
              className="w-full py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold flex items-center justify-center gap-1.5"
            >
              <Scissors size={13} /> Crop หน้าปัจจุบัน
            </button>
          ) : (
            <button
              type="button"
              onClick={handleActivateCrop}
              className="w-full py-2 bg-white border border-slate-200 text-slate-700 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 hover:bg-slate-50"
            >
              <Crop size={13} /> เปิดเครื่องมือ Crop
            </button>
          )
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <button
              type="button"
              onClick={handleModifyCrop}
              className="w-full py-2 bg-slate-800 hover:bg-slate-900 text-white rounded-lg text-xs font-bold flex items-center justify-center gap-1.5"
            >
              <RefreshCw size={13} /> แก้ไขกรอบ
            </button>

            <button
              type="button"
              onClick={handleCancelCrop}
              className="w-full py-2 bg-white border border-slate-200 text-slate-700 hover:bg-slate-50 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5"
            >
              <Crop size={13} /> ยกเลิก Crop
            </button>
          </div>
        )}

        {cropError && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-medium leading-5 text-amber-800">
            {cropError}
          </div>
        )}
      </div>
    </div>

    <div className="border-t border-slate-100 pt-4">
      <h3 className="text-xs font-bold text-[#172b4d] uppercase tracking-wider flex items-center gap-1.5">
        <FlipHorizontal size={13} className="text-slate-500" /> พลิกเอกสาร
      </h3>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => updateCurrentConfig({ flipH: !flipH })}
          className={`py-1.5 text-xs font-semibold rounded-lg border flex items-center justify-center gap-1.5 ${
            flipH
              ? "bg-blue-50 text-blue-700 border-blue-400 font-bold"
              : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
          }`}
        >
          <FlipHorizontal size={13} /> ซ้าย-ขวา
        </button>

        <button
          type="button"
          onClick={() => updateCurrentConfig({ flipV: !flipV })}
          className={`py-1.5 text-xs font-semibold rounded-lg border flex items-center justify-center gap-1.5 ${
            flipV
              ? "bg-blue-50 text-blue-700 border-blue-400 font-bold"
              : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
          }`}
        >
          <FlipVertical size={13} /> บน-ล่าง
        </button>
      </div>
    </div>

    <div className="border-t border-slate-100 pt-4 space-y-3">
      <div className="flex justify-between items-center">
        <h3 className="text-xs font-bold text-[#172b4d] uppercase tracking-wider flex items-center gap-1.5">
          <Maximize2 size={13} /> ปรับระนาบเอกสาร
        </h3>

        <button
          type="button"
          onClick={() =>
            updateCurrentConfig({ perspectiveV: 0, perspectiveH: 0 })
          }
          className="text-[10px] font-semibold text-slate-400 hover:text-slate-600"
        >
          <RefreshCw size={10} className="inline mr-0.5" /> ล้างมุมเอียง
        </button>
      </div>

      {[
        ["ปรับแนวตั้ง", perspectiveH, "perspectiveH"],
        ["ปรับแนวนอน", perspectiveV, "perspectiveV"],
      ].map(([label, value, key]) => (
        <div key={key as string} className="space-y-1">
          <div className="flex justify-between items-center text-xs">
            <span className="text-slate-500">{label}</span>
            <span className="text-slate-800 font-mono font-bold">
              {value as number}°
            </span>
          </div>

          <input
            type="range"
            min="-20"
            max="20"
            value={value as number}
            onChange={(e) =>
              updateCurrentConfig({
                [key as keyof PageConfig]: Number(e.target.value),
              })
            }
            className="w-full accent-blue-600 h-1 bg-slate-200 rounded cursor-pointer"
          />
        </div>
      ))}
    </div>

    {[
      {
        label: "หมุนเอกสาร",
        value: rotation,
        min: -180,
        max: 180,
        unit: "°",
        icon: <RotateCw size={12} />,
        key: "rotation" as keyof PageConfig,
        resetVal: 0,
        step: 90,
      },
      {
        label: "ความสว่าง",
        value: brightness,
        min: 50,
        max: 150,
        unit: "%",
        icon: <Sparkles size={12} />,
        key: "brightness" as keyof PageConfig,
        resetVal: 100,
        step: 5,
      },
      {
        label: "คอนทราสต์",
        value: contrast,
        min: 50,
        max: 150,
        unit: "%",
        icon: <Sparkles size={12} />,
        key: "contrast" as keyof PageConfig,
        resetVal: 100,
        step: 5,
      },
      {
        label: "ความคมชัดตัวอักษร",
        value: sharpness,
        min: 0,
        max: 100,
        unit: "%",
        icon: <Sparkles size={12} />,
        key: "sharpness" as keyof PageConfig,
        resetVal: 0,
        step: 10,
      },
    ].map((item) => (
      <div
        key={item.key}
        className="border-t border-slate-100 pt-4 space-y-2"
      >
        <div className="flex justify-between items-center">
          <label className="text-xs font-bold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
            {item.icon} {item.label}
          </label>

          <span className="text-xs font-mono font-bold text-slate-800">
            {item.value}
            {item.unit}
          </span>
        </div>

        <input
          type="range"
          min={item.min}
          max={item.max}
          value={Number(item.value)}
          onChange={(e) =>
            updateCurrentConfig({ [item.key]: Number(e.target.value) })
          }
          className="w-full accent-blue-600 h-1 bg-slate-200 rounded cursor-pointer"
        />

        <div className="flex gap-1.5 items-center pt-1">
          <button
            type="button"
            onClick={() =>
              updateCurrentConfig({
                [item.key]: Math.max(item.min, Number(item.value) - item.step),
              })
            }
            className="text-[10px] font-bold bg-white border border-slate-200 px-2.5 py-0.5 rounded text-slate-600 hover:bg-slate-50"
          >
            -{item.step}
          </button>

          <button
            type="button"
            onClick={() =>
              updateCurrentConfig({
                [item.key]: Math.min(item.max, Number(item.value) + item.step),
              })
            }
            className="text-[10px] font-bold bg-white border border-slate-200 px-2.5 py-0.5 rounded text-slate-600 hover:bg-slate-50"
          >
            +{item.step}
          </button>

          <button
            type="button"
            onClick={() => updateCurrentConfig({ [item.key]: item.resetVal })}
            className="text-[10px] font-semibold text-slate-400 ml-auto hover:text-slate-600"
          >
            <RefreshCw size={9} className="inline mr-0.5" />
            รีเซ็ต
          </button>
        </div>
      </div>
    ))}
  </div>

  <div className="-mt-px shrink-0 rounded-b-xl border border-t-0 border-slate-200 bg-white p-4 shadow-sm">
    <button
      type="button"
      disabled={isProcessing}
      onClick={handleConfirmAll}
      className="w-full px-6 bg-[#0052cc] hover:bg-[#0043a4] disabled:bg-slate-400 text-white py-3.5 rounded-xl text-xs font-bold tracking-wider uppercase shadow-md active:scale-98 transition-all flex items-center justify-center gap-2"
    >
      {isProcessing ? (
        <>
          <RefreshCw size={14} className="animate-spin" />
          กำลังประมวลผลเอกสาร...
        </>
      ) : (
        <>
          <Check size={14} />
          ยืนยันเอกสารและค้นหา Template
        </>
      )}
    </button>
  </div>
</div>
    </div>
  </div>
);
}
