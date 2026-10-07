"use client";

import { useEffect, useRef } from "react";

/** A glowing ribbon of sound-wave lines, drawn live on a canvas (no image to download).
 *
 *  Every line follows one shared centre path that sweeps from the top middle to the bottom
 *  right. The ribbon's width swings through zero along the way, so lines swap sides: that
 *  twist is what reads as 3D. Lines are drawn with additive blending ("lighter"), so where
 *  they cross the light adds up and glows. Most lines are dim red, a few are white.
 *
 *  Cost control: capped pixel ratio, paused when off screen or in a background tab, and a
 *  single still frame for people who ask their OS to reduce motion. */

type Props = { intensity?: number; className?: string };

const LINES = 110;
const PARTICLES = 160;
const RED = [228, 32, 43];

export function SoundRibbon({ intensity = 1, className = "" }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let w = 0;
    let h = 0;
    let frame = 0;
    let visible = true;
    const start = performance.now();
    // Deterministic per-line and per-particle randomness, so every load looks the same.
    const rand = (i: number) => ((Math.sin(i * 127.1) * 43758.5453) % 1 + 1) % 1;
    const bright = new Set([9, 22, 31, 44, 53, 61, 70, 86, 97]);

    function resize() {
      const r = canvas!.getBoundingClientRect();
      w = r.width;
      h = r.height;
      canvas!.width = Math.round(w * dpr);
      canvas!.height = Math.round(h * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    /** Point on line `k` (-1..1 across the ribbon) at position `u` (0..1 along it). */
    function point(u: number, k: number, t: number): [number, number] {
      const narrow = w < 640;
      const x0 = narrow ? -0.1 : 0.42;
      const x = w * (x0 + (1.15 - x0) * u);
      const cy = h * (narrow ? 0.5 + 0.5 * u : -0.12 + 1.1 * u) + h * 0.08 * Math.sin(u * 4.6 + t * 0.3);
      const width = h * (0.08 + 0.26 * Math.sin(Math.PI * Math.min(1, u * 1.1)));
      const twist = Math.cos(u * 3.6 - t * 0.22); // passes through 0: lines swap sides
      // Curvature across the ribbon (k² term): at the fold the lines fan out instead of
      // all meeting in one point, like a real band of fabric bending.
      const fold = width * 0.45 * (k * k - 0.35) * Math.sin(u * 3.1 + t * 0.18);
      const ripple = 4 * Math.sin(u * 22 + k * 3 + t * 1.3) * (0.3 + 0.7 * u);
      return [x, cy + k * width * twist + fold + ripple];
    }

    function draw(now: number) {
      const t = reduceMotion ? 2 : (now - start) / 1000;
      ctx!.clearRect(0, 0, w, h);
      ctx!.globalCompositeOperation = "lighter";
      const steps = Math.max(60, Math.round(w / 9));

      // Soft red light along the ribbon's path: cheap glow without blurring every line.
      for (let gI = 1; gI <= 7; gI++) {
        const [gx, gy] = point(gI / 8, 0, t);
        const r = h * 0.32;
        const glow = ctx!.createRadialGradient(gx, gy, 0, gx, gy, r);
        glow.addColorStop(0, `rgba(${RED.join(",")},${0.09 * intensity})`);
        glow.addColorStop(1, `rgba(${RED.join(",")},0)`);
        ctx!.fillStyle = glow;
        ctx!.fillRect(gx - r, gy - r, r * 2, r * 2);
      }

      for (let i = 0; i < LINES; i++) {
        const k = (i / (LINES - 1)) * 2 - 1;
        const isBright = bright.has(i);
        const a = (isBright ? 0.6 : 0.12 + 0.3 * rand(i)) * intensity;
        // Fade in from the left so the hero text keeps a clean black background.
        const g = ctx!.createLinearGradient(w * 0.38, 0, w, 0);
        const c = isBright ? "255,255,255" : RED.join(",");
        g.addColorStop(0, `rgba(${c},0)`);
        g.addColorStop(0.35, `rgba(${c},${a})`);
        g.addColorStop(1, `rgba(${c},${a * 0.8})`);
        ctx!.strokeStyle = g;
        ctx!.lineWidth = isBright ? 1.2 : 0.75;
        ctx!.shadowBlur = isBright ? 8 : 0; // only the few white lines get a real glow
        ctx!.shadowColor = `rgba(${RED.join(",")},0.9)`;
        ctx!.beginPath();
        for (let s = 0; s <= steps; s++) {
          const [x, y] = point(s / steps, k, t);
          if (s) ctx!.lineTo(x, y);
          else ctx!.moveTo(x, y);
        }
        ctx!.stroke();
      }

      ctx!.shadowBlur = 0;

      // Particles drift along the lines like sparks of signal.
      for (let p = 0; p < PARTICLES; p++) {
        const u = (rand(p + 500) + t * (0.015 + 0.02 * rand(p + 900))) % 1;
        if (u < 0.12) continue;
        const [x, y] = point(u, rand(p + 300) * 2 - 1, t);
        const flicker = 0.35 + 0.65 * Math.abs(Math.sin(t * 2 + p));
        ctx!.fillStyle = `rgba(255,255,255,${0.85 * flicker * intensity * Math.min(1, (u - 0.12) * 5)})`;
        ctx!.fillRect(x, y, 1.8, 1.8);
      }
      ctx!.globalCompositeOperation = "source-over";
    }

    function loop(now: number) {
      draw(now);
      if (!reduceMotion && visible && !document.hidden) frame = requestAnimationFrame(loop);
    }

    function restart() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(loop);
    }

    resize();
    restart();
    const ro = new ResizeObserver(() => {
      resize();
      restart();
    });
    ro.observe(canvas);
    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      if (visible) restart();
    });
    io.observe(canvas);
    document.addEventListener("visibilitychange", restart);

    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", restart);
    };
  }, [intensity]);

  return <canvas ref={ref} aria-hidden="true" className={`pointer-events-none absolute inset-0 h-full w-full ${className}`} />;
}
