// Create per workspace/source set, so a new upload cannot reuse stale images.
// Share in-flight work as well as completed crops. Bound retained image data.
export function createImageCropCache(
  load: (src: string) => Promise<HTMLImageElement>,
  maxImages = 2,
  maxCrops = 128,
) {
  const images = new Map<string, {
    image: Promise<HTMLImageElement>;
    crops: Map<string, Promise<string | null>>;
  }>();

  const entryFor = (src: string) => {
    let entry = images.get(src);
    if (!entry) {
      const image = Promise.resolve().then(() => load(src));
      entry = { image, crops: new Map() };
      images.set(src, entry);
      const created = entry;
      void image.catch(() => {
        if (images.get(src) === created) images.delete(src);
      });
      if (images.size > maxImages) images.delete(images.keys().next().value!);
    } else {
      images.delete(src);
      images.set(src, entry);
    }
    return entry;
  };

  return {
    getImage: (src: string) => entryFor(src).image,
    getCrop(src: string, key: string, render: (image: HTMLImageElement) => string | null) {
      const entry = entryFor(src);
      let crop = entry.crops.get(key);
      if (!crop) {
        crop = entry.image.then(render);
        entry.crops.set(key, crop);
        const pending = crop;
        void crop.then((result) => {
          if (result === null && entry.crops.get(key) === pending) entry.crops.delete(key);
        }, () => {
          if (entry.crops.get(key) === pending) entry.crops.delete(key);
        });
        if (entry.crops.size > maxCrops) entry.crops.delete(entry.crops.keys().next().value!);
      }
      return crop;
    },
  };
}

export const roiCropKey = (roi: {
  x: number; y: number; width: number; height: number;
  points?: { x: number; y: number }[];
}) => JSON.stringify([roi.x, roi.y, roi.width, roi.height, roi.points]);
