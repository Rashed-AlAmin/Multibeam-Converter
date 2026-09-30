"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  API,
  Job,
  Preview,
  STAGES,
  depthColor,
  formatBytes,
  formatCount,
  formatDuration,
} from "./lib";

const POLL_MS = 1000;

export default function Page() {
  const [file, setFile] = useState<File | null>(null);
  const [uploadPct, setUploadPct] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [fault, setFault] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const busy = uploading || job?.status === "queued" || job?.status === "running";

  // ---- selection ---------------------------------------------------------
  const choose = useCallback((picked: File | null) => {
    setFault(null);
    setPreview(null);
    setJob(null);
    setUploadPct(0);
    if (!picked) return;
    if (!picked.name.toLowerCase().endsWith(".all")) {
      setFault(`"${picked.name}" is not a .all file. Kongsberg raw sonar files only.`);
      setFile(null);
      return;
    }
    if (picked.size === 0) {
      setFault("That file is empty.");
      setFile(null);
      return;
    }
    setFile(picked);
  }, []);

  // ---- upload ------------------------------------------------------------
  function start() {
    if (!file) return;
    setUploading(true);
    setUploadPct(0);
    setFault(null);

    const body = new FormData();
    body.append("file", file);

    // XHR rather than fetch: only XHR reports upload progress, and a survey
    // file can be large enough that the user needs to see it moving.
    const xhr = new XMLHttpRequest();
    xhrRef.current = xhr;
    xhr.open("POST", `${API}/api/jobs`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) setUploadPct(e.loaded / e.total);
    };
    xhr.onload = () => {
      setUploading(false);
      xhrRef.current = null;
      if (xhr.status === 202) {
        setJob(JSON.parse(xhr.responseText) as Job);
      } else {
        let detail = `Upload failed (HTTP ${xhr.status}).`;
        try {
          detail = JSON.parse(xhr.responseText).detail ?? detail;
        } catch {
          /* non-JSON error body */
        }
        setFault(detail);
      }
    };
    xhr.onerror = () => {
      setUploading(false);
      xhrRef.current = null;
      setFault("Could not reach the converter. Is the backend running?");
    };
    xhr.onabort = () => {
      setUploading(false);
      xhrRef.current = null;
    };
    xhr.send(body);
  }

  function cancel() {
    xhrRef.current?.abort();
    if (job) void fetch(`${API}/api/jobs/${job.id}`, { method: "DELETE" });
    setJob(null);
    setUploadPct(0);
    setUploading(false);
  }

  function reset() {
    if (job) void fetch(`${API}/api/jobs/${job.id}`, { method: "DELETE" });
    setFile(null);
    setJob(null);
    setPreview(null);
    setFault(null);
    setUploadPct(0);
  }

  // ---- poll --------------------------------------------------------------
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "error") return;
    let alive = true;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/jobs/${job.id}`);
        if (!res.ok) throw new Error(`status ${res.status}`);
        const next = (await res.json()) as Job;
        if (alive) setJob(next);
      } catch {
        if (alive) setFault("Lost contact with the converter while it was working.");
      }
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [job]);

  // ---- preview -----------------------------------------------------------
  useEffect(() => {
    if (job?.status !== "done") return;
    let alive = true;
    void (async () => {
      try {
        const res = await fetch(`${API}/api/jobs/${job.id}/preview`);
        if (!res.ok) return;
        const data = (await res.json()) as Preview;
        if (alive) setPreview(data);
      } catch {
        /* preview is a bonus; never block the download on it */
      }
    })();
    return () => {
      alive = false;
    };
  }, [job?.status, job?.id]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !preview) return;
    drawPlanView(canvas, preview);
  }, [preview]);

  // ---- render ------------------------------------------------------------
  const s = job?.summary ?? null;

  return (
    <main className="wrap">
      <header>
        <h1>Multibeam Converter</h1>
        <p>
          Kongsberg <code>.all</code> &rarr; LAS point cloud, reprojected to WGS84 UTM.
        </p>
      </header>

      <section className="panel">
        {fault && (
          <div className="banner err">
            <div>
              <b>Something went wrong</b>
              {fault}
            </div>
          </div>
        )}

        {job?.status === "error" && (
          <div className="banner err">
            <div>
              <b>Conversion failed</b>
              {job.error ?? "The file could not be converted."}
            </div>
          </div>
        )}

        {/* --- pick a file --- */}
        {!file && !job && (
          <label
            className={`drop${dragOver ? " over" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              choose(e.dataTransfer.files?.[0] ?? null);
            }}
          >
            <strong>Drop a .all file here, or click to browse</strong>
            <span>Kongsberg multibeam raw data</span>
            <input
              type="file"
              accept=".all"
              onChange={(e) => choose(e.target.files?.[0] ?? null)}
            />
          </label>
        )}

        {/* --- selected, not started --- */}
        {file && !job && !uploading && (
          <>
            <div className="file-row">
              <div>
                <div className="name">{file.name}</div>
                <div className="size">{formatBytes(file.size)}</div>
              </div>
              <div className="actions">
                <button className="btn-ghost" onClick={reset}>
                  Choose another
                </button>
                <button className="btn-primary" onClick={start}>
                  Convert to LAS
                </button>
              </div>
            </div>
            <p className="note">
              The file is processed on the server with MB-System. Large surveys can take
              several minutes; you can leave this page open and watch the progress.
            </p>
          </>
        )}

        {/* --- uploading --- */}
        {uploading && (
          <>
            <div className="bar">
              <i style={{ width: `${Math.round(uploadPct * 100)}%` }} />
            </div>
            <div className="stage">
              <span>Uploading {file?.name}</span>
              <span>{Math.round(uploadPct * 100)}%</span>
            </div>
            <div className="actions" style={{ marginTop: 16 }}>
              <button className="btn-ghost" onClick={cancel}>
                Cancel
              </button>
            </div>
          </>
        )}

        {/* --- converting --- */}
        {job && (job.status === "queued" || job.status === "running") && (
          <>
            <div className="bar">
              <i style={{ width: `${Math.max(3, Math.round(job.progress * 100))}%` }} />
            </div>
            <div className="stage">
              <span>{job.message}</span>
              <span>{formatDuration(job.elapsed_seconds)}</span>
            </div>
            <ul className="steps">
              {STAGES.map((stage, i) => {
                const next = STAGES[i + 1];
                const active = job.progress >= stage.at && (!next || job.progress < next.at);
                const done = next ? job.progress >= next.at : job.progress >= 1;
                return (
                  <li key={stage.label} className={active ? "active" : done ? "done" : ""}>
                    <span className="dot" />
                    {stage.label}
                  </li>
                );
              })}
            </ul>
            <div className="actions" style={{ marginTop: 20 }}>
              <button className="btn-ghost" onClick={cancel}>
                Cancel
              </button>
            </div>
          </>
        )}

        {/* --- done --- */}
        {job?.status === "done" && s && (
          <>
            <div className="banner ok">
              <div>
                <b>Conversion complete</b>
                {formatCount(s.point_count)} soundings written in{" "}
                {formatDuration(job.elapsed_seconds)}.
              </div>
            </div>

            <div className="actions" style={{ marginBottom: 22 }}>
              <a href={`${API}/api/jobs/${job.id}/download`} download={job.download_name}>
                <button className="btn-primary">
                  Download {job.download_name} ({formatBytes(s.las_bytes)})
                </button>
              </a>
              <button className="btn-ghost" onClick={reset}>
                Convert another file
              </button>
            </div>

            <div className="grid">
              <div className="cell">
                <div className="k">Points</div>
                <div className="v">{formatCount(s.point_count)}</div>
                <div className="s">every beam, all pings</div>
              </div>
              <div className="cell">
                <div className="k">Depth range</div>
                <div className="v">
                  {s.depth_min_m.toFixed(1)} – {s.depth_max_m.toFixed(1)} m
                </div>
                <div className="s">below the vessel</div>
              </div>
              <div className="cell">
                <div className="k">UTM zone</div>
                <div className="v">
                  {s.utm_zone}
                  {s.utm_hemisphere}
                </div>
                <div className="s">EPSG:{s.epsg}</div>
              </div>
              <div className="cell">
                <div className="k">LAS size</div>
                <div className="v">{formatBytes(s.las_bytes)}</div>
                <div className="s">LAS 1.4, point format 1</div>
              </div>
            </div>

            <p className="note">
              <b>Z convention.</b> {s.z_convention}
            </p>

            {preview && (
              <div className="preview">
                <h3>Plan view</h3>
                <p className="cap">
                  {formatCount(preview.count)} of {formatCount(preview.total)} points,
                  evenly thinned. Colour is depth.
                </p>
                <canvas ref={canvasRef} width={1600} height={900} />
                <div className="scale">
                  <span>{s.depth_min_m.toFixed(0)} m</span>
                  <span className="ramp" />
                  <span>{s.depth_max_m.toFixed(0)} m</span>
                </div>
              </div>
            )}

            <details>
              <summary>Full conversion detail</summary>
              <table>
                <tbody>
                  <tr>
                    <td>Source file</td>
                    <td>
                      {job.original_name} ({formatBytes(job.upload_bytes)})
                    </td>
                  </tr>
                  <tr>
                    <td>MBIO format ID</td>
                    <td>{s.mbinfo_format || "—"}</td>
                  </tr>
                  <tr>
                    <td>Output CRS</td>
                    <td>{s.crs_name}</td>
                  </tr>
                  <tr>
                    <td>Longitude range</td>
                    <td>
                      {s.lon_min.toFixed(5)}° to {s.lon_max.toFixed(5)}°
                    </td>
                  </tr>
                  <tr>
                    <td>Latitude range</td>
                    <td>
                      {s.lat_min.toFixed(5)}° to {s.lat_max.toFixed(5)}°
                    </td>
                  </tr>
                  <tr>
                    <td>Easting range</td>
                    <td>
                      {formatCount(Math.round(s.easting_min_m))} to{" "}
                      {formatCount(Math.round(s.easting_max_m))} m
                    </td>
                  </tr>
                  <tr>
                    <td>Northing range</td>
                    <td>
                      {formatCount(Math.round(s.northing_min_m))} to{" "}
                      {formatCount(Math.round(s.northing_max_m))} m
                    </td>
                  </tr>
                </tbody>
              </table>
            </details>
          </>
        )}

        {job?.status === "error" && (
          <div className="actions">
            <button className="btn-primary" onClick={reset}>
              Try another file
            </button>
          </div>
        )}
      </section>

      <footer>
        Conversion performed by MB-System · reprojection by PROJ · LAS written with laspy
      </footer>
    </main>
  );
}

/**
 * Draw the swath from above: easting across, northing up, colour by depth.
 * The aspect ratio of the survey is preserved so the shape of the track is
 * honest rather than stretched to fill the canvas.
 */
function drawPlanView(canvas: HTMLCanvasElement, p: Preview) {
  const ctx = canvas.getContext("2d");
  if (!ctx || p.x.length === 0) return;

  const pad = 24;
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const xMin = Math.min(...p.x);
  const xMax = Math.max(...p.x);
  const yMin = Math.min(...p.y);
  const yMax = Math.max(...p.y);
  const zMin = Math.min(...p.z);
  const zMax = Math.max(...p.z);

  const spanX = Math.max(1e-6, xMax - xMin);
  const spanY = Math.max(1e-6, yMax - yMin);
  const scale = Math.min((W - pad * 2) / spanX, (H - pad * 2) / spanY);
  const offX = (W - spanX * scale) / 2;
  const offY = (H - spanY * scale) / 2;
  const spanZ = Math.max(1e-6, zMax - zMin);

  for (let i = 0; i < p.x.length; i++) {
    const px = offX + (p.x[i] - xMin) * scale;
    // Canvas y grows downward; northing grows upward, so it is flipped.
    const py = H - (offY + (p.y[i] - yMin) * scale);
    // z is elevation (negative down), so the deepest point maps to t = 1.
    const t = 1 - (p.z[i] - zMin) / spanZ;
    ctx.fillStyle = depthColor(t);
    ctx.fillRect(px, py, 2, 2);
  }
}
