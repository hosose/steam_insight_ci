import { useState, type ImgHTMLAttributes } from "react";

export function ImageWithFallback({ alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) {
  const [failed, setFailed] = useState(false);
  if (failed) return <div aria-label={alt} className="bg-[#10151f]" />;
  return <img {...props} alt={alt} onError={() => setFailed(true)} />;
}
