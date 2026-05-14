// Copies the Vite build output into the Python package's static dir so the
// FastAPI app can serve it without an extra build step.
import { cp, rm, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.resolve(here, "..", "dist");
const target = path.resolve(
  here,
  "..",
  "..",
  "src",
  "rtsp_recorder",
  "static",
);

await rm(target, { recursive: true, force: true });
await mkdir(target, { recursive: true });
await cp(dist, target, { recursive: true });
console.log(`Copied frontend/dist -> ${path.relative(process.cwd(), target)}`);
