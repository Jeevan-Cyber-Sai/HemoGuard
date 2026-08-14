import { useEffect, useRef } from "react";
import { MAX_POINTS } from "../lib/constants";

/**
 * 60-point trend on a canvas.
 *
 * `fixed` pins the Y axis (z-risk always reads against 0-15); otherwise the
 * axis auto-scales to the data with a small margin.
 */
export function Sparkline({ data, colour, fixed, min, max }) {
  const ref = useRef(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;

    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (!w || !h) return;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const pad = 4;

      ctx.strokeStyle = "#1a2035";
      ctx.lineWidth = 1;
      for (let i = 1; i < 4; i++) {
        const y = Math.round(pad + ((h - pad * 2) * i) / 4) + 0.5;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }

      if (data.length < 2) return;

      let lo, hi;
      if (fixed) {
        lo = min;
        hi = max;
      } else {
        lo = Math.min(...data);
        hi = Math.max(...data);
        if (hi - lo < 1e-9) {
          lo -= 0.5;
          hi += 0.5;
        } // flat line sits mid-card
        const margin = (hi - lo) * 0.12;
        lo -= margin;
        hi += margin;
      }
      const span = hi - lo || 1;

      // Anchor to MAX_POINTS so the trace grows in from the left instead of
      // rescaling horizontally on every reading.
      const stepX = (w - pad * 2) / (MAX_POINTS - 1);
      const pts = data.map((v, i) => [
        pad + i * stepX,
        pad + (h - pad * 2) * (1 - Math.min(1, Math.max(0, (v - lo) / span))),
      ]);

      // Quadratic segments through pair midpoints. A spline that overshoots
      // would draw a peak the sensor never measured.
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length - 1; i++) {
        const [cx, cy] = pts[i];
        const [nx, ny] = pts[i + 1];
        ctx.quadraticCurveTo(cx, cy, (cx + nx) / 2, (cy + ny) / 2);
      }
      const last = pts[pts.length - 1];
      ctx.lineTo(last[0], last[1]);
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1.6;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(last[0], last[1], 2.6, 0, Math.PI * 2);
      ctx.fillStyle = colour;
      ctx.fill();
    };

    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [data, colour, fixed, min, max]);

  return <canvas ref={ref} className="mt-1 block min-h-0 w-full flex-1" />;
}
