// Small shared helpers. Kept out of the component so the page file stays
// about the interface rather than about number formatting.

export const API = process.env.NEXT_PUBLIC_API_BASE ?? "";

export type Summary = {
  point_count: number;
  depth_min_m: number;
  depth_max_m: number;
  lon_min: number; lon_max: number;
  lat_min: number; lat_max: number;
  utm_zone: number;
  utm_hemisphere: string;
  epsg: number;
  crs_name: string;
  easting_min_m: number; easting_max_m: number;
  northing_min_m: number; northing_max_m: number;
  mbinfo_format: string;
  las_bytes: number;
  z_convention: string;
};

export type Job = {
  id: string;
  original_name: string;
  upload_bytes: number;
  status: "queued" | "running" | "done" | "error";
  progress: number;
  message: string;
  error: string | null;
  summary: Summary | null;
  download_name: string;
  elapsed_seconds: number;
};

export type Preview = {
  count: number;
  total: number;
  origin: [number, number, number];
  x: number[];
  y: number[];
  z: number[];
};

export function formatBytes(n: number): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds - m * 60)}s`;
}

// Approximation of the viridis ramp: perceptually even, and readable for
// viewers with colour-vision deficiency. t runs 0 (shallow) to 1 (deep).
const VIRIDIS: [number, number, number][] = [
  [253, 231, 37],
  [94, 201, 98],
  [33, 145, 140],
  [59, 82, 139],
  [68, 1, 84],
];

export function depthColor(t: number): string {
  const clamped = Math.min(1, Math.max(0, t));
  const scaled = clamped * (VIRIDIS.length - 1);
  const i = Math.min(VIRIDIS.length - 2, Math.floor(scaled));
  const f = scaled - i;
  const a = VIRIDIS[i];
  const b = VIRIDIS[i + 1];
  const mix = (k: number) => Math.round(a[k] + (b[k] - a[k]) * f);
  return `rgb(${mix(0)},${mix(1)},${mix(2)})`;
}

export const STAGES = [
  { at: 0.05, label: "Inspect file (mbinfo)" },
  { at: 0.2, label: "Preprocess to .mb59" },
  { at: 0.45, label: "Export soundings (mblist)" },
  { at: 0.78, label: "Reproject to UTM" },
  { at: 0.9, label: "Write LAS" },
];
