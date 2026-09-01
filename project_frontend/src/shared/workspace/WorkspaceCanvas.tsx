"use client";

import React, { useCallback, useEffect, useRef } from "react";
import { WorkspaceImageMetrics } from "./roiGeometry";

interface WorkspaceCanvasProps {
  imageSrc: string;
  children?: React.ReactNode;
  imageRef?: React.RefObject<HTMLImageElement | null>;
  width?: number;
  zoom?: number;
  onImageMetricsChange?: (metrics: WorkspaceImageMetrics) => void;
  onMouseDown?: React.MouseEventHandler<HTMLDivElement>;
  onMouseMove?: React.MouseEventHandler<HTMLDivElement>;
  onMouseUp?: React.MouseEventHandler<HTMLDivElement>;
  onMouseLeave?: React.MouseEventHandler<HTMLDivElement>;
  onDoubleClick?: React.MouseEventHandler<HTMLDivElement>;
  className?: string;
}

export default function WorkspaceCanvas({
  imageSrc,
  children,
  imageRef,
  width = 750,
  zoom = 1,
  onImageMetricsChange,
  onMouseDown,
  onMouseMove,
  onMouseUp,
  onMouseLeave,
  onDoubleClick,
  className = "",
}: WorkspaceCanvasProps) {
  const imageWrapperRef = useRef<HTMLDivElement | null>(null);
  const internalImageRef = useRef<HTMLImageElement | null>(null);

  const setImageRef = useCallback(
    (node: HTMLImageElement | null) => {
      internalImageRef.current = node;
      if (imageRef) {
        (imageRef as React.MutableRefObject<HTMLImageElement | null>).current = node;
      }
    },
    [imageRef]
  );

  const measureImage = useCallback(() => {
    const wrapper = imageWrapperRef.current;
    const image = internalImageRef.current;
    if (!wrapper || !image || !onImageMetricsChange) return;

    const safeZoom = zoom || 1;
    const wrapperRect = wrapper.getBoundingClientRect();
    const imageRect = image.getBoundingClientRect();
    const elementOffsetX = (imageRect.left - wrapperRect.left) / safeZoom;
    const elementOffsetY = (imageRect.top - wrapperRect.top) / safeZoom;
    const elementWidth = imageRect.width / safeZoom;
    const elementHeight = imageRect.height / safeZoom;
    const objectFit = window.getComputedStyle(image).objectFit;
    const naturalAspect = image.naturalWidth > 0 && image.naturalHeight > 0 ? image.naturalWidth / image.naturalHeight : 1;
    const elementAspect = elementHeight > 0 ? elementWidth / elementHeight : naturalAspect;
    let imageOffsetX = elementOffsetX;
    let imageOffsetY = elementOffsetY;
    let imageWidth = elementWidth;
    let imageHeight = elementHeight;

    if (objectFit === "contain" && elementWidth > 0 && elementHeight > 0) {
      if (elementAspect > naturalAspect) {
        imageHeight = elementHeight;
        imageWidth = imageHeight * naturalAspect;
        imageOffsetX = elementOffsetX + (elementWidth - imageWidth) / 2;
      } else {
        imageWidth = elementWidth;
        imageHeight = imageWidth / naturalAspect;
        imageOffsetY = elementOffsetY + (elementHeight - imageHeight) / 2;
      }
    }

    onImageMetricsChange({
      imageOffsetX,
      imageOffsetY,
      imageWidth,
      imageHeight,
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
    });
  }, [onImageMetricsChange, zoom]);

  useEffect(() => {
    measureImage();
  }, [imageSrc, measureImage, width, zoom]);

  useEffect(() => {
    if (!onImageMetricsChange) return;

    const handleResize = () => measureImage();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [measureImage, onImageMetricsChange]);

  return (
    <div className={`flex-1 min-w-0 bg-[#edf2f7] border border-slate-200 rounded-xl overflow-auto p-6 shadow-inner ${className}`}>
      <div
        className="relative inline-block"
        style={{
          transform: `scale(${zoom})`,
          transformOrigin: "top left",
          transition: "transform 0.1s ease-out",
        }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseLeave}
        onDoubleClick={onDoubleClick}
      >
        <div ref={imageWrapperRef} className="relative bg-transparent" style={{ width }}>
          {imageSrc && (
            <img
              ref={setImageRef}
              src={imageSrc}
              alt="Workspace page"
              draggable="false"
              onLoad={measureImage}
              className="w-full h-auto block select-none pointer-events-none border border-slate-300 shadow-xl rounded bg-white"
            />
          )}
          {children}
        </div>
      </div>
    </div>
  );
}
