import puppeteer from "puppeteer-core";
import { readdir, readFile, mkdir, writeFile, stat } from "fs/promises";
import { join, basename } from "path";

const LOTTIE_DIR = process.argv[2] || "assets/lottie";
const OUTPUT_DIR = process.argv[3] || "assets/sprite_sheets";
const SIZE = 128;
const COLS = 20;
const LOTTIE_JS =
  process.env.HOME +
  "/.nvm/versions/node/v24.15.0/lib/node_modules/lottie-web/build/player/lottie.min.js";

async function renderOne(lottieScript, file) {
  const name = basename(file, ".json");
  const outPath = join(OUTPUT_DIR, name + ".png");

  try {
    await stat(outPath);
    console.log(`  SKIP ${name} (exists)`);
    return null;
  } catch {}

  const lottieData = JSON.parse(await readFile(join(LOTTIE_DIR, file), "utf-8"));
  const browser = await puppeteer.launch({
    executablePath: "/usr/bin/google-chrome-stable",
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--single-process"],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: SIZE, height: SIZE, deviceScaleFactor: 1 });
    await page.setContent(
      "<html><body style='margin:0;background:#0000'><div id='c' style='width:" + SIZE + "px;height:" + SIZE + "px'></div></body></html>"
    );
    await page.evaluate(lottieScript);

    const totalFrames = await page.evaluate(
      (data) =>
        new Promise((resolve) => {
          const a = lottie.loadAnimation({
            container: document.getElementById("c"),
            renderer: "html",
            loop: false,
            autoplay: false,
            animationData: data,
          });
          a.addEventListener("DOMLoaded", () => {
            window._a = a;
            resolve(Math.ceil(a.totalFrames));
          });
        }),
      lottieData
    );

    const step = Math.max(1, Math.floor(totalFrames / 60));
    const frames = [];
    for (let f = 0; f < totalFrames; f += step) frames.push(f);
    const rows = Math.ceil(frames.length / COLS);
    const sheetW = COLS * SIZE;
    const sheetH = rows * SIZE;

    const el = await page.evaluateHandle(() => document.getElementById("c"));

    await page.evaluate(
      (w, h) => {
        const cv = document.createElement("canvas");
        cv.id = "s";
        cv.width = w;
        cv.height = h;
        document.body.appendChild(cv);
      },
      sheetW,
      sheetH
    );

    for (let i = 0; i < frames.length; i++) {
      await page.evaluate((fr) => window._a.goToAndStop(fr, true), frames[i]);
      await new Promise((r) => setTimeout(r, 10));
      const buf = await el.screenshot({ type: "png", omitBackground: true });
      const b64 = buf.toString("base64");
      const col = i % COLS;
      const row = Math.floor(i / COLS);
      await page.evaluate(
        (d, x, y, w, h) =>
          new Promise((r) => {
            const img = new Image();
            img.onload = () => {
              document.getElementById("s").getContext("2d").drawImage(img, x, y, w, h);
              r();
            };
            img.src = "data:image/png;base64," + d;
          }),
        b64,
        col * SIZE,
        row * SIZE,
        SIZE,
        SIZE
      );
    }

    const sheetB64 = await page.evaluate(() =>
      document.getElementById("s").toDataURL("image/png").split(",")[1]
    );

    await writeFile(outPath, Buffer.from(sheetB64, "base64"));
    const st = await stat(outPath);
    console.log(`  ${name}: ${frames.length} frames, ${(st.size / 1024).toFixed(0)}KB`);
    return { frames: frames.length, cols: COLS, rows, sheetW, sheetH, fps: 10, size: st.size };
  } finally {
    await browser.close();
  }
}

async function main() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  const files = (await readdir(LOTTIE_DIR)).filter(
    (f) => f.endsWith(".json") && f !== "metrics.json"
  );
  const lottieScript = await readFile(LOTTIE_JS, "utf-8");
  const manifest = {};

  for (const file of files) {
    const name = basename(file, ".json");
    console.log(`Rendering ${name}...`);
    try {
      const result = await renderOne(lottieScript, file);
      if (result) manifest[name] = result;
      else {
        const st = await stat(join(OUTPUT_DIR, name + ".png"));
        manifest[name] = { size: st.size };
      }
    } catch (e) {
      console.log(`  ERROR: ${e.message}`);
    }
  }

  await writeFile(join(OUTPUT_DIR, "manifest.json"), JSON.stringify(manifest, null, 2));
  const totalSize = Object.values(manifest).reduce((s, v) => s + (v.size || 0), 0);
  console.log(`Done! Total: ${(totalSize / 1024 / 1024).toFixed(1)}MB`);
}

main().catch(console.error);
