import { cp, mkdir, readFile, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const extensionRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(extensionRoot, "..");
const outputRoot = join(repositoryRoot, "dist");
const unpacked = join(outputRoot, "ctrip-silent-collector");
const archive = join(outputRoot, "ctrip-silent-collector.zip");
const files = [
  "manifest.json",
  "popup.html",
  "popup.css",
  "popup.js",
  "README.md"
];
const directories = ["src", "config"];

await rm(unpacked, { recursive: true, force: true });
await rm(archive, { force: true });
await mkdir(unpacked, { recursive: true });

for (const file of files) {
  await cp(join(extensionRoot, file), join(unpacked, file));
}
for (const directory of directories) {
  await cp(join(extensionRoot, directory), join(unpacked, directory), { recursive: true });
}

const manifest = JSON.parse(await readFile(join(unpacked, "manifest.json"), "utf8"));
if (manifest.manifest_version !== 3 || manifest.action?.default_popup !== "popup.html") {
  throw new Error("Packaged manifest is not the expected MV3 popup build.");
}

const zipped = spawnSync("zip", ["-q", "-r", archive, "."], {
  cwd: unpacked,
  encoding: "utf8"
});
if (zipped.status !== 0) {
  throw new Error(`zip failed: ${zipped.stderr || zipped.stdout || zipped.status}`);
}

console.log(JSON.stringify({ unpacked, archive, manifest_version: manifest.manifest_version }));
