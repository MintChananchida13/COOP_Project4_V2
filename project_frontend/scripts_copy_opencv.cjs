const fs = require("node:fs");
const path = require("node:path");
const target = path.join(__dirname, "public", "opencv.js");
fs.mkdirSync(path.dirname(target), { recursive: true });
fs.copyFileSync(require.resolve("@techstark/opencv-js/dist/opencv.js"), target);
