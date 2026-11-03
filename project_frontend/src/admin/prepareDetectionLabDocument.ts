import { convertPdfToImages } from "../shared/pdfImages";
import { autoCropDetectionImage } from "./autoCropDetectionImage";

/** Match user upload/adjust: render PDFs, crop single pages, retain multipage pages. */
export async function prepareDetectionLabDocument(file: File, signal: AbortSignal): Promise<File[]> {
  signal.throwIfAborted();
  if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
    return [await autoCropDetectionImage(file, signal)];
  }
  const images = await convertPdfToImages(file);
  signal.throwIfAborted();
  if (!images.length) throw new Error("ไม่พบหน้าที่สามารถอ่านได้ใน PDF");
  const pages: File[] = [];
  for (const [index, image] of images.entries()) {
    signal.throwIfAborted();
    const blob = await (await fetch(image, { signal })).blob();
    pages.push(new File([blob], `${file.name.replace(/\.pdf$/i, "")}-page-${index + 1}.jpg`, { type: "image/jpeg" }));
  }
  return pages.length === 1 ? [await autoCropDetectionImage(pages[0], signal, "pdf")] : pages;
}
