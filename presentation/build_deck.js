// PixelMosaic project deck: microservices on Google Cloud.
// Rebuild after every project update:  node build_deck.js   (writes MosaicArt_24_25.pptx)
// Needs the pptx skill's apply_theme.js (PPTX_SKILL) and the packages in package.json.
const path = require("path");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");
// apply_theme.js loads jszip from pptxgenjs; let it resolve packages from this folder
process.env.NODE_PATH = path.join(__dirname, "node_modules");
require("module").Module._initPaths();
const PPTX_SKILL = process.env.PPTX_SKILL ||
  "C:/Users/james/.claude/skills/synced/cf0625e3-2662-4182-ac0f-e9833779160e_cd5ebf38-a69e-4b2d-9f8a-34197e4e8d3b/pptx";
const { applyTheme } = require(path.join(PPTX_SKILL, "scripts", "apply_theme.js"));

const OUT = process.argv[2] || "MosaicArt_24_25.pptx";
const LIVE_URL = process.env.LIVE_URL || "http://8.233.196.233";

const THEME = {
  name: "PixelMosaic",
  headFontFace: "Arial",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "0B0F19", // near-black navy (dark slides)
    lt1: "FFFFFF",
    dk2: "334155", // slate (secondary text on light)
    lt2: "F1F4F9", // light slide background
    accent1: "0E7490", // deep cyan (on light)
    accent2: "BE185D", // magenta (on light)
    accent3: "6D28D9", // violet
    accent4: "22D3EE", // bright cyan (on dark)
    accent5: "F472B6", // pink (on dark)
    accent6: "94A3B8", // muted slate
    hlink: "0E7490",
    folHlink: "6D28D9",
  },
};
const HEX = THEME.colors;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "Crowd-Sourced Pixel Art Mosaic Generator";
pres.subject = "Cloud computing project: BRD and implementation";
const C = pres.SchemeColor;

// ---------------------------------------------------------------- icons
async function iconPng(name, hex, px = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(fa[name], { color: "#" + hex, size: px })
  );
  const buf = await sharp(Buffer.from(svg)).resize(px, px).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

// ---------------------------------------------------------------- layouts
const W = 13.333;
const MX = 0.6;
const CW = W - 2 * MX;

function contentLayout(name, dark) {
  pres.defineSlideMaster({
    title: name,
    background: { color: dark ? C.text1 : C.background2 },
    margin: [0.5, 0.6, 0.5, 0.6],
    objects: [
      {
        placeholder: {
          options: {
            name: "kicker", type: "body", x: MX, y: 0.42, w: CW, h: 0.32,
            fontSize: 12, bold: true, charSpacing: 3, color: dark ? C.accent4 : C.accent1, margin: 0, valign: "middle", align: "left",
          },
          text: "",
        },
      },
      {
        placeholder: {
          options: {
            name: "title", type: "title", x: MX, y: 0.74, w: CW, h: 0.8,
            fontSize: 34, bold: true, color: dark ? C.background1 : C.text1, margin: 0, valign: "middle", align: "left",
          },
          text: "",
        },
      },
    ],
    slideNumber: { x: W - MX - 0.6, y: 7.0, w: 0.6, h: 0.3, fontSize: 10, color: C.accent6, align: "right" },
  });
}
contentLayout("CONTENT_LIGHT", false);
contentLayout("CONTENT_DARK", true);

pres.defineSlideMaster({
  title: "COVER",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "kicker", type: "body", x: MX, y: 1.05, w: 7.2, h: 0.35, fontSize: 13, bold: true, charSpacing: 4, color: C.accent4, margin: 0, align: "left" }, text: "" } },
    { placeholder: { options: { name: "title", type: "title", x: MX, y: 1.6, w: 7.4, h: 2.9, fontSize: 52, bold: true, color: C.background1, margin: 0, valign: "top", align: "left" }, text: "" } },
    { placeholder: { options: { name: "subtitle", type: "body", x: MX, y: 4.6, w: 7.2, h: 0.9, fontSize: 20, color: C.accent6, margin: 0, valign: "top", align: "left" }, text: "" } },
    { placeholder: { options: { name: "byline", type: "body", x: MX, y: 6.3, w: 9.0, h: 0.4, fontSize: 14, color: C.accent6, margin: 0, align: "left" }, text: "" } },
  ],
});

// ---------------------------------------------------------------- helpers
const TILE_PALETTE = [HEX.accent4, HEX.accent5, HEX.accent3, "1E293B", HEX.accent1, HEX.accent2];

function mosaicMotif(slide, x0, y0, cols, rows, size, gap, seed = 7) {
  let s = seed;
  const rnd = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const hex = TILE_PALETTE[Math.floor(rnd() * TILE_PALETTE.length)];
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: x0 + c * (size + gap), y: y0 + r * (size + gap), w: size, h: size,
        fill: { color: hex, transparency: hex === "1E293B" ? 0 : 8 }, line: { type: "none" }, rectRadius: 0.06,
        objectName: `tile-${r}-${c}`,
      });
    }
  }
}

function card(slide, x, y, w, h, { dark = false, name = "card" } = {}) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: 0.12,
    fill: { color: dark ? "151B2B" : C.background1 },
    line: { color: dark ? "26304A" : "DCE3EE", width: 1 },
    shadow: dark ? undefined : { type: "outer", color: "0B0F19", opacity: 0.08, blur: 6, offset: 2, angle: 90 },
    objectName: name,
  });
}

function text(slide, str, opts) {
  slide.addText(str, { isTextBox: true, margin: 0, valign: "top", ...opts });
}

async function iconBadge(slide, iconName, x, y, size, fillHex, iconHex) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w: size, h: size, rectRadius: 0.1, fill: { color: fillHex }, line: { type: "none" },
    objectName: `badge-${iconName}`,
  });
  const pad = size * 0.24;
  slide.addImage({ data: await iconPng(iconName, iconHex), x: x + pad, y: y + pad, w: size - 2 * pad, h: size - 2 * pad, altText: iconName.replace(/^Fa/, "") });
}

function arrow(slide, x1, y1, x2, y2, hex = HEX.dk2, both = false) {
  slide.addShape(pres.shapes.LINE, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1) || 0.001, h: Math.abs(y2 - y1) || 0.001,
    flipH: x2 < x1, flipV: y2 < y1,
    line: { color: hex, width: 1.75, endArrowType: "triangle", beginArrowType: both ? "triangle" : undefined },
  });
}

function addSlide(layout, section, kicker, title) {
  const s = pres.addSlide({ masterName: layout, sectionTitle: section });
  if (kicker) s.addText(kicker, { placeholder: "kicker" });
  if (title) s.addText(title, { placeholder: "title" });
  return s;
}

// ================================================================ slides
(async () => {
  // ---------------------------------------------------------- 1. cover
  pres.addSection({ title: "Introduction" });
  {
    const s = pres.addSlide({ masterName: "COVER", sectionTitle: "Introduction" });
    s.addText("CLOUD COMPUTING PROJECT · BRD", { placeholder: "kicker" });
    s.addText("Crowd-Sourced Pixel Art Mosaic Generator", { placeholder: "title" });
    s.addText("A photo mosaic platform built as microservices on Google Cloud", { placeholder: "subtitle" });
    s.addText([
      { text: "Roll No. 24 & 25", options: { bold: true, color: C.background1 } },
      { text: " · [Name 1] & [Name 2] · [College / course]" },
    ], { placeholder: "byline" });
    mosaicMotif(s, 8.65, 1.35, 8, 8, 0.44, 0.1, 11);
    s.addNotes("Introduce the project: a platform that turns thousands of photos into one large photo mosaic. It is built as separate microservices and deployed on Google Cloud. Fill in your names and course on this slide.");
  }

  // ---------------------------------------------------------- 2. objective
  {
    const s = addSlide("CONTENT_DARK", "Introduction", "1 · PRODUCT OBJECTIVE", "");
    text(s, [
      { text: "Turn thousands of photos into ", options: { color: C.background1 } },
      { text: "one giant, high-resolution photo mosaic", options: { color: C.accent4 } },
      { text: ", rendered by a cluster of cloud machines.", options: { color: C.background1 } },
    ], { x: MX, y: 1.05, w: 11.2, h: 1.9, fontSize: 34, bold: true, fontFace: THEME.headFontFace });

    text(s, "Two sources of tiles (dual-asset sourcing):", { x: MX, y: 3.55, w: 8, h: 0.35, fontSize: 15, color: C.accent6 });
    const items = [
      ["FaImages", "Community library", "A shared pool everyone can donate to, filterable by category tags", HEX.accent4],
      ["FaUserLock", "Personal vault", "Your own photos: saved to your account, or temporary for guests", HEX.accent5],
    ];
    for (let i = 0; i < items.length; i++) {
      const [ic, head, body, hex] = items[i];
      const x = MX + i * 4.25;
      card(s, x, 4.05, 4.0, 1.65, { dark: true, name: `source-${i}` });
      await iconBadge(s, ic, x + 0.3, 4.3, 0.6, "1E293B", hex);
      text(s, head, { x: x + 1.1, y: 4.3, w: 2.7, h: 0.4, fontSize: 18, bold: true, color: hex === HEX.accent4 ? C.accent4 : C.accent5 });
      text(s, body, { x: x + 1.1, y: 4.75, w: 2.7, h: 0.85, fontSize: 14, color: C.accent6 });
    }
    card(s, MX + 8.5, 4.05, 3.63, 1.65, { dark: true, name: "future-source" });
    await iconBadge(s, "FaQrcode", MX + 8.8, 4.3, 0.6, "1E293B", HEX.accent6);
    text(s, "Live event submissions", { x: MX + 9.55, y: 4.3, w: 2.5, h: 0.4, fontSize: 16, bold: true, color: C.accent6 });
    text(s, "Moved to future scope", { x: MX + 9.55, y: 4.75, w: 2.5, h: 0.4, fontSize: 14, italic: true, color: C.accent6 });
    s.addNotes("The objective: give artists and communities a way to build one large artwork from many photos. Tiles come from the shared community library and from each user's private vault. Live event submissions were part of the original plan and are now future scope.");
  }

  // ---------------------------------------------------------- 3. why cloud
  pres.addSection({ title: "Cloud architecture" });
  {
    const s = addSlide("CONTENT_LIGHT", "Cloud architecture", "2 · WHY CLOUD COMPUTING", "How the app benefits from Google Cloud");
    const head = { bold: true, color: C.background1, fill: { color: C.text1 }, fontSize: 14 };
    const rows = [
      ["Benefit", "What it means for PixelMosaic", "Google Cloud feature behind it"].map((t) => ({ text: t, options: head })),
      ["Scales with demand", "A big render gets more workers; quiet hours shrink back to one VM per service", "Managed Instance Groups + autoscaler (1–7 VMs)"],
      ["Pay only for what runs", "The heavy image maths runs on discounted Spot VMs; everything else on small e2 machines", "Spot VMs, e2 machine types"],
      ["Survives failures", "A crashed or preempted VM is replaced, and another worker finishes its piece of the render", "Health checks, auto-healing, lock leases in Cloud Storage"],
      ["One secure way in", "Users only ever reach the website; services and storage stay private", "Cloud Load Balancing, VPC firewall, Cloud NAT"],
      ["Durable data, no database", "Photos, accounts, jobs and results outlive any VM, with no database to run", "Cloud Storage (4 private buckets)"],
      ["Repeatable, zero-downtime releases", "One script rebuilds every service and rolls it out VM by VM", "Cloud Build, Artifact Registry, Terraform"],
    ];
    s.addTable(rows.map((r, i) => i === 0 ? r : r.map((t, j) => ({
      text: t, options: { fontSize: 13, color: C.text1, bold: j === 0, fill: { color: i % 2 ? C.background1 : "E7ECF5" } },
    }))), {
      x: MX, y: 1.85, w: CW, colW: [2.75, 5.5, 3.88], rowH: 0.66, border: { type: "solid", color: "CBD5E1", pt: 0.75 },
      valign: "middle", margin: [0.05, 0.13, 0.05, 0.13],
    });
    s.addNotes("Why this project belongs on the cloud. Rendering is bursty: a large mosaic needs many CPUs for a short time, so autoscaling groups grow for the render and shrink back afterwards, and Spot VMs make that heavy maths cheap. Failures are routine and handled automatically. Users only reach the website through the load balancer. All state lives in Cloud Storage, so any VM can be replaced at any time without losing photos, accounts or results. Releases are one command and roll out without downtime.");
  }

  // ---------------------------------------------------------- 4. microservice architecture
  {
    const s = addSlide("CONTENT_LIGHT", "Cloud architecture", "3 · MICROSERVICE ARCHITECTURE", "Four microservices, each on its own VMs");
    const y1 = 1.85, h1 = 1.3, bw = 2.0, step = 2.52;
    const row1 = [
      ["FaUsers", "Users", "Browsers", "E2E8F0", HEX.dk2],
      ["FaScaleBalanced", "External HTTP(S) LB", "the only public entry", "EDE9FE", HEX.accent3],
      ["FaGlobe", "Frontend", "nginx + React · e2-micro", "CFFAFE", HEX.accent1],
      ["FaNetworkWired", "Internal TCP LB", "private IP :8000", "EDE9FE", HEX.accent3],
      ["FaShieldHalved", "API Gateway", "FastAPI · e2-medium", "CFFAFE", HEX.accent1],
    ];

    // private VPC boundary (drawn first, behind the boxes)
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 5.45, y: 1.6, w: 7.38, h: 5.3, rectRadius: 0.15,
      fill: { color: "FFFFFF", transparency: 40 }, line: { color: "94A3B8", width: 1.25, dashType: "dash" }, objectName: "vpc-boundary",
    });
    text(s, "Private VPC · no public IPs · firewall-restricted", { x: 5.65, y: 6.55, w: 6, h: 0.3, fontSize: 11, italic: true, color: C.text2 });

    for (let i = 0; i < row1.length; i++) {
      const [ic, head, sub, fill, iconHex] = row1[i];
      const x = MX + i * step;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: y1, w: bw, h: h1, rectRadius: 0.1, fill: { color: fill }, line: { color: "CBD5E1", width: 0.75 }, objectName: `svc-${head}` });
      s.addImage({ data: await iconPng(ic, iconHex), x: x + bw / 2 - 0.2, y: y1 + 0.13, w: 0.4, h: 0.4, altText: head });
      text(s, head, { x: x + 0.05, y: y1 + 0.58, w: bw - 0.1, h: 0.32, fontSize: 13, bold: true, color: C.text1, align: "center" });
      text(s, sub, { x: x + 0.05, y: y1 + 0.9, w: bw - 0.1, h: 0.3, fontSize: 10.5, color: C.text2, align: "center" });
      if (i < row1.length - 1) arrow(s, x + bw + 0.04, y1 + h1 / 2, x + step - 0.04, y1 + h1 / 2, HEX.dk2);
    }
    text(s, "/api/*", { x: 7.68, y: y1 + h1 / 2 - 0.36, w: 0.5, h: 0.25, fontSize: 10, color: C.text2, align: "center" });

    // row 2: headless services
    const y2 = 3.75, h2 = 1.2;
    const row2 = [
      ["FaPuzzlePiece", "Assembler", "stitches the final image · e2-medium", 6.0],
      ["FaMicrochip", "Workers ×N", "4 render engines · Spot e2-small", 8.6],
    ];
    for (const [ic, head, sub, x] of row2) {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: y2, w: 2.3, h: h2, rectRadius: 0.1, fill: { color: "FCE7F3" }, line: { color: "CBD5E1", width: 0.75 }, objectName: `svc-${head}` });
      s.addImage({ data: await iconPng(ic, HEX.accent2), x: x + 0.15, y: y2 + 0.15, w: 0.38, h: 0.38, altText: head });
      text(s, head, { x: x + 0.62, y: y2 + 0.17, w: 1.6, h: 0.34, fontSize: 13, bold: true, color: C.text1 });
      text(s, sub, { x: x + 0.15, y: y2 + 0.62, w: 2.05, h: 0.5, fontSize: 10.5, color: C.text2 });
      arrow(s, x + 1.15, y2 + h2 + 0.05, x + 1.15, 5.5, HEX.accent2, true);
    }

    // row 3: storage
    const y3 = 5.55;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 6.0, y: y3, w: 6.68, h: 0.9, rectRadius: 0.1, fill: { color: C.text1 }, line: { type: "none" }, objectName: "storage" });
    s.addImage({ data: await iconPng("FaBucket", HEX.accent4), x: 6.18, y: y3 + 0.24, w: 0.42, h: 0.42, altText: "Cloud Storage" });
    text(s, "Cloud Storage · 4 private buckets", { x: 6.75, y: y3 + 0.1, w: 5.8, h: 0.35, fontSize: 14, bold: true, color: C.background1 });
    text(s, "photo-library · target-posters · task-coordination · mosaic-outputs", { x: 6.75, y: y3 + 0.47, w: 5.8, h: 0.3, fontSize: 11, color: C.accent4 });
    arrow(s, 11.68, y1 + h1 + 0.05, 11.68, y3 - 0.05, HEX.accent1, true);
    text(s, "accounts, uploads,\njob tasks", { x: 11.78, y: 3.75, w: 1.0, h: 0.6, fontSize: 10, color: C.text2 });

    // build & deploy column
    text(s, "Build & deploy", { x: MX, y: 3.6, w: 4.6, h: 0.32, fontSize: 13, bold: true, color: C.text1 });
    const build = [
      ["FaCode", "Terraform", "creates every resource"],
      ["FaHammer", "Cloud Build", "builds the 4 images"],
      ["FaBoxesStacked", "Artifact Registry", "VMs pull images at boot"],
    ];
    for (let i = 0; i < build.length; i++) {
      const [ic, head, sub] = build[i];
      const y = 4.02 + i * 0.86;
      card(s, MX, y, 4.5, 0.72, { name: `build-${i}` });
      s.addImage({ data: await iconPng(ic, HEX.accent3), x: MX + 0.18, y: y + 0.17, w: 0.38, h: 0.38, altText: head });
      text(s, head, { x: MX + 0.72, y: y + 0.1, w: 3.6, h: 0.3, fontSize: 13, bold: true, color: C.text1 });
      text(s, sub, { x: MX + 0.72, y: y + 0.38, w: 3.6, h: 0.28, fontSize: 11, color: C.text2 });
    }
    s.addNotes("Walk the request path left to right. Users reach only the external load balancer, which forwards to the frontend VMs. The frontend sends /api requests through a private internal load balancer to the API Gateway. The gateway, the workers and the assembler share nothing except the private buckets: the gateway handles accounts and uploads and writes job tasks, workers pull tasks and write results, the assembler stitches the final image. Workers and the assembler accept no inbound traffic. On the left: Terraform creates everything, Cloud Build builds the images, and VMs pull them from Artifact Registry.");
  }

  // ---------------------------------------------------------- 4b. technologies
  {
    const s = addSlide("CONTENT_LIGHT", "Cloud architecture", "TECHNOLOGY STACK", "Technologies and where they run");
    const head = { bold: true, color: C.background1, fill: { color: C.text1 }, fontSize: 13 };
    const rows = [
      ["Where", "Technologies"].map((t) => ({ text: t, options: head })),
      ["Frontend VMs", "React 18, Vite 5, nginx 1.27"],
      ["API Gateway VMs", "Python 3.11, FastAPI, Uvicorn, Pillow, bcrypt"],
      ["Worker VMs (Spot)", "Python 3.11, NumPy, OpenCV, SciPy"],
      ["Assembler VMs", "Python 3.11, OpenCV, NumPy"],
      ["Every VM", "Docker on Container-Optimized OS"],
      ["Storage", "Google Cloud Storage (MinIO locally)"],
      ["Network", "Cloud Load Balancing, VPC, Cloud NAT"],
      ["Build & deploy", "Cloud Build, Artifact Registry, Terraform"],
      ["Local & testing", "Docker Compose, fake-gcs-server, unittest, Playwright"],
    ];
    s.addTable(rows.map((r, i) => i === 0 ? r : r.map((t, j) => ({
      text: t, options: { fontSize: 13, color: C.text1, bold: j === 0, fill: { color: i % 2 ? C.background1 : "E7ECF5" } },
    }))), {
      x: MX, y: 1.8, w: CW, colW: [3.2, CW - 3.2], rowH: 0.47, border: { type: "solid", color: "CBD5E1", pt: 0.75 },
      valign: "middle", margin: [0.04, 0.13, 0.04, 0.13],
    });
    s.addNotes("The technology used in each part of the system.");
  }

  // ---------------------------------------------------------- 5. one way in
  {
    const s = addSlide("CONTENT_DARK", "Cloud architecture", "3 · SECURITY BOUNDARIES", "One way in, nothing else exposed");
    const items = [
      ["FaDoorOpen", "Single entry point", "The external load balancer forwards only to the frontend. No VM has a public IP address."],
      ["FaShieldHalved", "One API Gateway", "Firewall rules let only frontend VMs reach the gateway; every data request passes through it."],
      ["FaLock", "Private buckets", "Public access prevention is enforced; browsers never contact storage directly."],
      ["FaFileShield", "Checked uploads", "Each upload gets a single-use, expiring ticket; size and file type are verified before storage."],
      ["FaIdBadge", "Least-privilege identities", "One service account per microservice, holding only the bucket roles it needs."],
      ["FaUserLock", "Accounts & private vaults", "bcrypt passwords and signed HttpOnly cookies; account and guest vaults are readable only by their owner."],
    ];
    for (let i = 0; i < items.length; i++) {
      const [ic, head, body] = items[i];
      const col = i % 3, row = Math.floor(i / 3);
      const x = MX + col * 4.1, y = 1.9 + row * 2.45;
      card(s, x, y, 3.85, 2.2, { dark: true, name: `boundary-${i}` });
      await iconBadge(s, ic, x + 0.28, y + 0.28, 0.62, "1E293B", i % 2 ? HEX.accent5 : HEX.accent4);
      text(s, head, { x: x + 0.28, y: y + 1.0, w: 3.35, h: 0.4, fontSize: 17, bold: true, color: C.background1 });
      text(s, body, { x: x + 0.28, y: y + 1.42, w: 3.35, h: 0.72, fontSize: 13, color: C.accent6 });
    }
    s.addNotes("These are the guarantees the deployment enforces. Users can only ever reach the frontend. Storage is private, uploads are relayed through the gateway with single-use tickets, each service has its own minimal identity, and personal vaults are tied to signed-in accounts.");
  }

  // ---------------------------------------------------------- 6. GCP services: compute
  pres.addSection({ title: "Google Cloud services" });
  {
    const s = addSlide("CONTENT_LIGHT", "Google Cloud services", "GOOGLE CLOUD SERVICES · 1 OF 2", "Compute and autoscaling");
    const items = [
      ["FaServer", "Compute Engine", "Every microservice runs on its own VMs, on Container-Optimized OS"],
      ["FaClone", "Instance templates", "One per service: machine type, image, start-up script, identity"],
      ["FaLayerGroup", "Managed Instance Groups", "Regional groups spread VMs across zones in asia-south1"],
      ["FaArrowsUpDown", "Autoscaler", "Every group scales between 1 and 7 VMs on CPU utilisation"],
      ["FaBolt", "Spot VMs", "Workers run on discounted Spot capacity; lock leases recover preempted work"],
      ["FaHeartPulse", "Health checks & auto-healing", "A VM whose service stops answering is recreated automatically"],
      ["FaArrowsRotate", "Rolling updates", "New images replace VMs gradually; connection draining lets old VMs finish requests"],
      ["FaShieldHalved", "Shielded VMs", "Secure boot, vTPM and integrity monitoring on every VM"],
    ];
    await iconGrid(s, items, 4, HEX.accent1, "CFFAFE");
    s.addNotes("The compute side. Each microservice has an instance template and a regional managed instance group with its own autoscaler, from 1 to 7 VMs. Health checks drive auto-healing, and workers use Spot VMs to keep the heavy maths cheap.");
  }

  // ---------------------------------------------------------- 7. GCP services: network, storage, delivery
  {
    const s = addSlide("CONTENT_LIGHT", "Google Cloud services", "GOOGLE CLOUD SERVICES · 2 OF 2", "Network, storage and delivery");
    const items = [
      ["FaScaleBalanced", "Cloud Load Balancing", "External Application LB for users; internal TCP LB in front of the gateway"],
      ["FaNetworkWired", "VPC & firewall rules", "Custom private network; only the listed paths are allowed in"],
      ["FaRoute", "Cloud NAT & Private Google Access", "Outbound access and Google APIs without public IP addresses"],
      ["FaBucket", "Cloud Storage", "4 private buckets with lifecycle rules: the only data store, accounts included"],
      ["FaHammer", "Cloud Build", "Builds the four container images from source in the cloud"],
      ["FaBoxesStacked", "Artifact Registry", "Private Docker repository that every VM pulls from"],
      ["FaIdBadge", "IAM service accounts", "Five identities: one per microservice, plus one for builds"],
      ["FaChartLine", "Cloud Logging & Monitoring", "Container logs and metrics from every VM"],
    ];
    await iconGrid(s, items, 4, HEX.accent3, "EDE9FE");
    s.addNotes("The supporting services: two load balancers, a private VPC with firewall rules and Cloud NAT, private Cloud Storage buckets as the only data store, and Cloud Build plus Artifact Registry for images. IAM gives each service its own identity, and Logging collects every container's output.");
  }

  // ---------------------------------------------------------- 8. deployment sizing
  {
    const s = addSlide("CONTENT_LIGHT", "Google Cloud services", "DEPLOYMENT · asia-south1 (MUMBAI)", "Smallest machines that do the job");
    const head = { bold: true, color: C.background1, fill: { color: C.text1 }, fontSize: 14 };
    const rows = [
      ["Microservice", "Machine", "VMs", "Scales on", "Health check", "Reachable from"].map((t) => ({ text: t, options: head })),
      ["Frontend", "e2-micro · 1 GB", "1 – 7", "CPU 60%", "/healthz :80", "Internet, via external LB"],
      ["API Gateway", "e2-medium · 4 GB", "1 – 7", "CPU 60%", "/health :8000", "Frontend VMs only"],
      ["Workers", "e2-small · 2 GB · Spot", "1 – 7", "CPU 75%", "/health :8080", "Nothing (health checks only)"],
      ["Assembler", "e2-medium · 4 GB", "1 – 7", "CPU 70%", "/health :8080", "Nothing (health checks only)"],
    ];
    s.addTable(rows.map((r, i) => i === 0 ? r : r.map((t, j) => ({
      text: t, options: { fontSize: 14, color: C.text1, bold: j === 0, fill: { color: i % 2 ? C.background1 : "E7ECF5" } },
    }))), {
      x: MX, y: 1.85, w: CW, colW: [1.9, 2.55, 0.95, 1.35, 1.9, 3.48], rowH: 0.62,
      border: { type: "solid", color: "CBD5E1", pt: 0.75 }, valign: "middle", margin: [0.05, 0.12, 0.05, 0.12],
    });
    const notes = [
      ["FaGaugeHigh", "Why these sizes", "The gateway and assembler hold up to 12,000-pixel images in memory (about 1–1.5 GB); the frontend only serves files."],
      ["FaLayerGroup", "One microservice per VM", "Every VM runs exactly one service container; no two microservices ever share a machine."],
    ];
    for (let i = 0; i < notes.length; i++) {
      const [ic, head, body] = notes[i];
      const x = MX + i * 6.15;
      card(s, x, 5.25, 5.95, 1.45, { name: `sizing-note-${i}` });
      await iconBadge(s, ic, x + 0.25, 5.5, 0.55, "CFFAFE", HEX.accent1);
      text(s, head, { x: x + 1.0, y: 5.42, w: 4.75, h: 0.35, fontSize: 15, bold: true, color: C.text1 });
      text(s, body, { x: x + 1.0, y: 5.8, w: 4.75, h: 0.8, fontSize: 13, color: C.text2 });
    }
    s.addNotes("Each group runs between 1 and 7 VMs. Machines are the smallest that work: the frontend is an e2-micro, the gateway and assembler need about 4 GB for large images, and workers are small Spot VMs. The project's quota is 12 vCPUs, so small machines give the autoscaler the most headroom.");
  }

  // ---------------------------------------------------------- 9. studio
  pres.addSection({ title: "Product" });
  {
    const s = addSlide("CONTENT_DARK", "Product", "8.1 · PHOTO MOSAIC STUDIO", "A full creative studio");
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: MX, y: 1.8, w: 7.75, h: 4.91, rectRadius: 0.1, fill: { color: "26304A" }, line: { type: "none" }, objectName: "screenshot-frame" });
    s.addImage({ path: "studio.png", x: MX + 0.06, y: 1.86, w: 7.63, h: 4.83, altText: "Studio Workshop after rendering a 60 × 40 photomosaic" });
    text(s, "Controls from the BRD", { x: 8.75, y: 1.85, w: 4.0, h: 0.4, fontSize: 18, bold: true, color: C.background1 });
    const bullets = [
      "Engines: photomosaic, true-colour ASCII, Kuwahara oil, flow field",
      "Blend slider, tag filters, grid density, no-repeat radius",
      "Alpha-tint slider 0–50%, adjustable live",
      "Tiles fly into place once processing finishes",
      "Click any tile: original photo, contributor, ΔE₀₀",
      "Sharp 256 px tiles when zoomed past 200%",
      "Export PNG or JPEG up to 12,000 px",
      "Personal vault saved to your account",
    ];
    text(s, bullets.map((b, i) => ({ text: b, options: { bullet: true, breakLine: i < bullets.length - 1 } })),
      { x: 8.75, y: 2.35, w: 4.0, h: 4.3, fontSize: 15, color: C.accent6, paraSpaceAfter: 7 });
    s.addNotes("The Studio is the main product. Every control in the BRD works: the engine picker, the personal-versus-community blend slider, strict tag filters, grid density, the no-repeat radius, and an alpha-tint slider you can still adjust after rendering. While the workers process, the canvas shows a ghosted version of the target; once every chunk is done, all the tiles fly in from the edges and dock into place. Click any tile to see the original photo, who contributed it and its colour-match score. Zooming past 200% swaps in sharp 256-pixel tiles, and large exports are stitched from the original photos. Exports go up to 12,000 pixels as PNG or JPEG.");
  }

  // ---------------------------------------------------------- 9b. accounts
  {
    const s = addSlide("CONTENT_LIGHT", "Product", "8.1 · ACCOUNTS & PERSONAL VAULT", "Your photos follow your account");
    const steps = [
      ["FaUserPlus", "Optional sign-up", "Guests can make mosaics from their own photos, but those are deleted when they leave. Signing up keeps them."],
      ["FaCloudArrowUp", "Upload to your vault", "Photos are analysed by the workers and saved under your account in Cloud Storage."],
      ["FaLaptop", "Sign in anywhere", "Another browser or device shows the same vault, with nothing to re-upload."],
      ["FaUserGear", "Manage it yourself", "The Account page shows storage used (500 MB quota), deletes chosen photos, or erases the account."],
    ];
    for (let i = 0; i < steps.length; i++) {
      const [ic, head, body] = steps[i];
      const y = 1.85 + i * 1.22;
      card(s, MX, y, 6.3, 1.08, { name: `account-step-${i}` });
      await iconBadge(s, ic, MX + 0.25, y + 0.22, 0.62, i === 3 ? "FCE7F3" : "CFFAFE", i === 3 ? HEX.accent2 : HEX.accent1);
      text(s, `${i + 1} · ${head}`, { x: MX + 1.1, y: y + 0.12, w: 5.0, h: 0.36, fontSize: 16, bold: true, color: C.text1 });
      text(s, body, { x: MX + 1.1, y: y + 0.5, w: 5.0, h: 0.52, fontSize: 12.5, color: C.text2 });
    }

    const px = 7.2, pw = W - MX - px;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: px, y: 1.85, w: pw, h: 4.75, rectRadius: 0.12, fill: { color: C.text1 }, line: { type: "none" }, objectName: "account-storage" });
    await iconBadge(s, "FaBucket", px + 0.3, 2.1, 0.6, "1E293B", HEX.accent4);
    text(s, "Stored in buckets, no database", { x: px + 1.05, y: 2.18, w: pw - 1.3, h: 0.45, fontSize: 17, bold: true, color: C.background1 });
    const files = [
      ["users/{username}.json", "profile + bcrypt hash"],
      ["user-emails/{sha256}.json", "one account per email"],
      ["auth/signing-key.json", "session key shared by every gateway VM"],
      ["personal/{user_id or gst_…}/…", "the vault's photos (guest vaults expire after a day)"],
    ];
    for (let i = 0; i < files.length; i++) {
      const y = 2.95 + i * 0.68;
      text(s, files[i][0], { x: px + 0.3, y, w: pw - 0.6, h: 0.3, fontSize: 13, bold: true, color: C.accent4, fontFace: "Courier New" });
      text(s, files[i][1], { x: px + 0.3, y: y + 0.3, w: pw - 0.6, h: 0.3, fontSize: 12, color: C.accent6 });
    }
    text(s, "Signed HttpOnly session cookie · lockout after 10 failed logins · forgot-password link (email delivery is future scope)",
      { x: px + 0.3, y: 5.75, w: pw - 0.6, h: 0.7, fontSize: 11.5, italic: true, color: C.accent6 });
    s.addNotes("Signing in is optional. Guests can upload their own photos and make mosaics straight away, but their vault only lasts for that browser session: the Studio shows a clear warning, the browser asks before they leave, and a storage lifecycle rule deletes guest photos after a day. If a guest signs up or signs in, everything they uploaded moves into their account. Accounts make the personal vault persistent. A user signs up once; photos they upload are saved under their account id in Cloud Storage, so signing in on any other device shows the same vault. There is still no database: each account is a small JSON file in a private bucket, and usernames and emails are claimed with create-if-absent writes so they can't be taken twice. Sessions are signed cookies that any gateway VM can verify. Forgot password works as a stub: the reset link is written to the server log until an email service is added. The Account page gives each user full control: their profile, storage used against a 500 MB quota, a grid to delete individual photos, and a danger zone that deletes the account and every personal photo after they type their username or password.");
  }

  // ---------------------------------------------------------- 10. engines
  {
    const s = addSlide("CONTENT_LIGHT", "Product", "4 · MULTI-ENGINE COMPUTE CORE", "Four render engines, all on CPUs");
    const engines = [
      ["FaTableCells", "Photomosaic", "Two-stage search: a matrix product shortlists 256 photos per cell, CIEDE2000 picks the best. No-repeat radius, personal / community blend."],
      ["FaFont", "Sobel ASCII", "CLAHE-equalised brightness picks from a 70-glyph ramp; strong edges become ─ │ ╱ ╲. True Color paints each glyph in the photo's own colour."],
      ["FaPaintbrush", "Kuwahara oil", "Each pixel takes the mean colour of the calmest of its four quadrant windows. Fully vectorised box filters: a 4K frame in about 0.3 s."],
      ["FaWind", "Vector flow field", "Brush strokes follow the edge direction, θ = atan2(Gy, Gx) + π/2, traced in RK2 steps and painted in the photo's colours."],
    ];
    const fills = ["CFFAFE", "FCE7F3", "EDE9FE", "CFFAFE"];
    const inks = [HEX.accent1, HEX.accent2, HEX.accent3, HEX.accent1];
    const ew = (CW - 3 * 0.25) / 4;
    for (let i = 0; i < engines.length; i++) {
      const [ic, head, body] = engines[i];
      const x = MX + i * (ew + 0.25);
      card(s, x, 1.9, ew, 4.7, { name: `engine-${i}` });
      await iconBadge(s, ic, x + 0.28, 2.2, 0.7, fills[i], inks[i]);
      text(s, `MODE ${i + 1}`, { x: x + 0.28, y: 3.12, w: ew - 0.56, h: 0.3, fontSize: 12, bold: true, charSpacing: 2, color: C.accent6 });
      text(s, head, { x: x + 0.28, y: 3.42, w: ew - 0.56, h: 0.45, fontSize: 19, bold: true, color: C.text1 });
      text(s, body, { x: x + 0.28, y: 4.0, w: ew - 0.56, h: 2.4, fontSize: 13, color: C.text2 });
    }
    s.addNotes("Four engines, all on ordinary CPUs. Photomosaic is the core: it compares colours perceptually in CIELAB space and matches each tile's internal layout. It searches in two stages: a single matrix product finds the 256 closest photos for every cell, then the exact CIEDE2000 formula picks among them, which matches 1,700 cells against 11,800 photos in about a second with the same result as a full search. ASCII equalises brightness with CLAHE so midtones such as skin use the whole glyph ramp, turns the strongest edges into line glyphs, and can paint every glyph in the photo's own colour. Kuwahara gives an oil-paint look by averaging each pixel's calmest neighbourhood quadrant; it is computed entirely with box filters, so a 4K frame takes about 0.3 seconds. The flow field paints brush strokes along the edges of the picture.");
  }

  // ---------------------------------------------------------- 11. distributed render
  {
    const s = addSlide("CONTENT_DARK", "Product", "WHY IT'S DISTRIBUTED", "One render, shared between many workers");
    const stats = [
      ["2,700", "tiles in a 60 × 45 grid", C.accent4],
      ["9", "row-band chunks", C.accent4],
      ["3", "workers at the same time", C.accent5],
      ["~7 s", "for an 8,000 px image", C.accent5],
    ];
    for (let i = 0; i < stats.length; i++) {
      const [num, label, color] = stats[i];
      const x = MX + i * 3.08;
      text(s, num, { x, y: 2.2, w: 2.5, h: 1.2, fontSize: 54, bold: true, color, fontFace: THEME.headFontFace });
      text(s, label, { x, y: 3.45, w: 2.6, h: 0.6, fontSize: 15, color: C.accent6 });
      if (i < stats.length - 1) text(s, "→", { x: x + 2.58, y: 2.42, w: 0.45, h: 0.8, fontSize: 28, color: C.accent6 });
    }
    text(s, [
      { text: "Each worker ", options: {} },
      { text: "claims", options: { bold: true, color: C.background1 } },
      { text: " a chunk by writing a lock file in the bucket and keeps renewing it while it works, so no two workers do the same piece. If a Spot VM is preempted, its lock expires after ", options: {} },
      { text: "120 seconds", options: { bold: true, color: C.background1 } },
      { text: " and another worker finishes the chunk. Neighbouring bands run in two waves, so the no-repeat rule holds across chunk boundaries.", options: {} },
    ], { x: MX, y: 4.75, w: 11.6, h: 1.5, fontSize: 16, color: C.accent6 });
    s.addNotes("These are measured numbers from the local cluster: a 60-column grid on a 4:3 target gives 2,700 tiles, split into 9 row bands and processed by 3 workers, producing a 7,980 × 5,985 image in about 7 seconds. Coordination is entirely through lock files in the bucket.");
  }

  // ---------------------------------------------------------- 12. observability
  pres.addSection({ title: "Operations" });
  {
    const s = addSlide("CONTENT_LIGHT", "Operations", "6 & 8.2 · OBSERVABILITY AND ADMIN", "System numbers stay behind the scenes");
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 5.25, y: 1.8, w: 7.48, h: 4.8, rectRadius: 0.1, fill: { color: C.text1 }, line: { type: "none" }, objectName: "admin-frame" });
    s.addImage({ path: "admin.png", x: 5.31, y: 1.86, w: 7.36, h: 4.66, altText: "Admin cluster health dashboard with live worker nodes and charts" });
    const items = [
      ["FaChartLine", "What admins see", "Live nodes, CPU per worker and per core, tiles per second, queue depth; live pulse, every 5 s"],
      ["FaArrowsUpDown", "Scaling history", "Node count over time, plus every VM joining or leaving the cluster"],
      ["FaImages", "Community moderation", "Browse every community photo and delete any of them"],
      ["FaUserShield", "Who sees it", "Unlisted URL, never linked or indexed; deleting needs an admin account"],
    ];
    for (let i = 0; i < items.length; i++) {
      const [ic, head, body] = items[i];
      const y = 1.85 + i * 1.2;
      await iconBadge(s, ic, MX, y, 0.6, "CFFAFE", HEX.accent1);
      text(s, head, { x: MX + 0.8, y: y - 0.02, w: 3.7, h: 0.35, fontSize: 16, bold: true, color: C.text1 });
      text(s, body, { x: MX + 0.8, y: y + 0.36, w: 3.7, h: 0.75, fontSize: 13, color: C.text2 });
    }
    s.addNotes("Workers publish a heartbeat to the coordination bucket every three seconds, with real CPU readings and throughput counters. The admin dashboard aggregates them. The dashboard refreshes every five seconds. The same page has a moderation grid of every community photo; deleting one removes its original, tiles and index entry, and requires a signed-in admin account. The page is only reachable on an unlisted URL; audience pages never show cluster data.");
  }

  // ---------------------------------------------------------- 13. dual environment
  {
    const s = addSlide("CONTENT_LIGHT", "Operations", "9 · DUAL ENVIRONMENT: WRITE ONCE, RUN ANYWHERE", "Same code on a laptop and on Google Cloud");
    const head = { bold: true, color: C.background1, fill: { color: C.text1 }, fontSize: 14 };
    const rows = [
      ["Setting", "LOCAL (Docker Compose)", "PROD (Google Cloud)"].map((t) => ({ text: t, options: head })),
      ["USE_GCP", "false", "true"],
      ["Storage", "MinIO, private network only", "Cloud Storage, 4 private buckets"],
      ["Entry point", "Frontend on port 3000, the only open port", "External Application Load Balancer"],
      ["Workers", "Compose replicas", "Spot VM group, autoscaled 1–7"],
      ["Health", "Container health checks", "LB health checks + MIG auto-healing"],
      ["Images", "docker compose build", "Cloud Build → Artifact Registry"],
      ["Setup", "docker-compose.yml", "Terraform + one deploy script"],
    ];
    s.addTable(rows.map((r, i) => i === 0 ? r : r.map((t, j) => ({
      text: t, options: { fontSize: 14, color: C.text1, bold: j === 0, fill: { color: i % 2 ? C.background1 : "E7ECF5" } },
    }))), {
      x: MX, y: 1.85, w: CW, colW: [2.2, 4.75, 5.18], rowH: 0.55,
      border: { type: "solid", color: "CBD5E1", pt: 0.75 }, valign: "middle", margin: [0.05, 0.12, 0.05, 0.12],
    });
    text(s, "The production code path was also tested locally against a Cloud Storage emulator before deploying.", { x: MX, y: 6.45, w: CW, h: 0.35, fontSize: 12, italic: true, color: C.text2 });
    s.addNotes("The same images run in both environments; only environment variables change. Locally the network layout mirrors the cloud firewall: the frontend is the only open port. Before deploying, the full test suite also ran against a local Cloud Storage emulator in production mode.");
  }

  // ---------------------------------------------------------- 14. delivered
  pres.addSection({ title: "Status and next steps" });
  {
    const s = addSlide("CONTENT_LIGHT", "Status and next steps", "PROGRESS AGAINST THE BRD", "Core platform delivered and deployed");
    const done = [
      "Bucket-only storage, MinIO ⇄ Cloud Storage (§3, §9)",
      "Photomosaic, ASCII, Kuwahara and flow-field engines (§4)",
      "Blend slider, strict tag filters, no-repeat, live alpha-tint (§8.1)",
      "Optional accounts: guest vaults, saved vaults, self-service /account",
      "Fly-in assembly, tile inspector, community moderation",
      "Drag-and-drop uploads through the API Gateway (§4.1)",
      "Live cluster telemetry on an unlisted admin URL (§6, §8.2)",
      "Terraform deployment: 4 autoscaled VM groups, load balancers (§2)",
      "Search-friendly landing page; 43 automated tests",
    ];
    card(s, MX, 1.85, 7.7, 4.85, { name: "done-card" });
    await iconBadge(s, "FaCheck", MX + 0.35, 2.1, 0.62, "CFFAFE", HEX.accent1);
    text(s, "Delivered", { x: MX + 1.15, y: 2.15, w: 6, h: 0.5, fontSize: 22, bold: true, color: C.text1 });
    text(s, done.map((b, i) => ({ text: b, options: { bullet: true, breakLine: i < done.length - 1 } })),
      { x: MX + 0.4, y: 2.9, w: 7.0, h: 3.7, fontSize: 14.5, color: C.text1, paraSpaceAfter: 4 });

    // Deployment result callout
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 8.6, y: 1.85, w: 4.13, h: 4.85, rectRadius: 0.12, fill: { color: C.text1 }, line: { type: "none" }, objectName: "live-card" });
    await iconBadge(s, "FaCloud", 8.9, 2.1, 0.62, "1E293B", HEX.accent4);
    text(s, "Live on Google Cloud", { x: 8.9, y: 2.9, w: 3.6, h: 0.4, fontSize: 18, bold: true, color: C.background1 });
    text(s, LIVE_URL || "Deployed", { x: 8.9, y: 3.35, w: 3.6, h: 0.4, fontSize: 16, bold: true, color: C.accent4, hyperlink: LIVE_URL ? { url: LIVE_URL } : undefined });
    text(s, "44 s", { x: 8.9, y: 4.05, w: 3.6, h: 0.9, fontSize: 48, bold: true, color: C.accent5, fontFace: THEME.headFontFace });
    text(s, "end to end on the cloud: 30 photos uploaded, a 2,400-tile mosaic rendered, exported and the vault cleared", { x: 8.9, y: 5.0, w: 3.6, h: 1.5, fontSize: 13, color: C.accent6 });
    s.addNotes("Everything in the core scope is built, tested end to end, and deployed to Google Cloud, including user accounts with a personal vault that persists across devices. There are 43 automated tests: engine unit tests plus end-to-end tests that also ran against the live deployment. The automated browser test ran against the live deployment: upload 30 photos, render a 60 by 40 mosaic, export it and clear the vault, in 44 seconds.");
  }

  // ---------------------------------------------------------- 15. future scope
  {
    const s = addSlide("CONTENT_DARK", "Status and next steps", "FUTURE SCOPE", "What comes next");
    card(s, MX, 1.85, 6.2, 4.85, { dark: true, name: "live-arena-card" });
    await iconBadge(s, "FaQrcode", MX + 0.35, 2.15, 0.75, "1E293B", HEX.accent5);
    text(s, "Live Event Arena", { x: MX + 1.3, y: 2.2, w: 4.7, h: 0.55, fontSize: 24, bold: true, color: C.background1 });
    text(s, "Moved from core scope", { x: MX + 1.3, y: 2.7, w: 4.7, h: 0.35, fontSize: 13, italic: true, color: C.accent5 });
    const arena = [
      "Organizer uploads the main picture and gets a QR code",
      "Attendees scan it and add a selfie from their phone",
      "The big screen reveals each new tile with a spotlight",
      "\"Find My Photo\" zooms back to a guest's code, e.g. #T-2506",
      "Groundwork in place: event state and tokens already live in buckets",
    ];
    text(s, arena.map((b, i) => ({ text: b, options: { bullet: true, breakLine: i < arena.length - 1 } })),
      { x: MX + 0.4, y: 3.35, w: 5.5, h: 3.1, fontSize: 15, color: C.accent6, paraSpaceAfter: 8 });

    const later = [
      ["FaBell", "Event-driven ingestion", "Start workers from Cloud Storage events via Pub/Sub, not polling"],
      ["FaEnvelope", "Password-reset email", "Email the reset link; today it is written to the server log"],
      ["FaKey", "Admin-only dashboards", "Put the read-only telemetry behind the admin role too"],
      ["FaLock", "HTTPS on a custom domain", "Google-managed certificate on the existing load balancer"],
      ["FaChartLine", "Monitoring dashboards & alerts", "Cloud Monitoring dashboards and alerting for the cluster"],
      ["FaMagnifyingGlassPlus", "Deep-zoom viewer & time-lapse", "Pan-and-zoom tile pyramids and a replay of the assembly"],
    ];
    for (let i = 0; i < later.length; i++) {
      const [ic, head, body] = later[i];
      const y = 1.85 + i * 0.81;
      await iconBadge(s, ic, 7.15, y + 0.06, 0.5, "1E293B", HEX.accent4);
      text(s, head, { x: 7.85, y, w: 4.9, h: 0.32, fontSize: 15, bold: true, color: C.background1 });
      text(s, body, { x: 7.85, y: y + 0.33, w: 4.9, h: 0.42, fontSize: 12, color: C.accent6 });
    }
    s.addNotes("The Live Event Arena is the main item moved to future scope: QR-code joining, phone selfies, a spotlight on the big screen, and Find My Photo. Much of its groundwork already exists in the gateway, since its state is already stored in buckets. Everything else not yet built is listed on the right: event-driven ingestion with Pub/Sub, emailing password-reset links (today a stub that logs the link), putting the read-only dashboards behind the admin role that already guards moderation, HTTPS on a custom domain, Cloud Monitoring dashboards and alerts, and the deep-zoom viewer with an assembly time-lapse.");
  }

  // ---------------------------------------------------------- 16. thank you
  {
    const s = pres.addSlide({ masterName: "COVER", sectionTitle: "Status and next steps" });
    s.addText("PIXEL MOSAIC PLATFORM", { placeholder: "kicker" });
    s.addText("Thank you", { placeholder: "title" });
    s.addText(LIVE_URL
      ? [{ text: "Questions? Try it live at " }, { text: LIVE_URL, options: { color: C.accent4, bold: true, hyperlink: { url: LIVE_URL } } }]
      : "Questions? We're happy to run a live demo.", { placeholder: "subtitle" });
    s.addText("[Name 1] & [Name 2]", { placeholder: "byline" });
    mosaicMotif(s, 8.65, 1.35, 8, 8, 0.44, 0.1, 29);
    s.addNotes("Close and invite questions. The deployed application is live on Google Cloud at the address on the slide.");
  }

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})().catch((e) => { console.error(e); process.exit(1); });

// 4-column icon grid of short service cards
async function iconGrid(s, items, cols, iconHex, badgeFill) {
  const gapX = 0.3, gapY = 0.22;
  const w = (CW - (cols - 1) * gapX) / cols;
  const h = 2.4;
  for (let i = 0; i < items.length; i++) {
    const [ic, head, body] = items[i];
    const col = i % cols, row = Math.floor(i / cols);
    const x = MX + col * (w + gapX), y = 1.85 + row * (h + gapY);
    card(s, x, y, w, h, { name: `service-${i}` });
    await iconBadge(s, ic, x + 0.25, y + 0.25, 0.58, badgeFill, iconHex);
    text(s, head, { x: x + 0.25, y: y + 0.92, w: w - 0.5, h: 0.6, fontSize: 15, bold: true, color: C.text1, valign: "bottom" });
    text(s, body, { x: x + 0.25, y: y + 1.6, w: w - 0.5, h: 0.72, fontSize: 12, color: C.text2 });
  }
}
