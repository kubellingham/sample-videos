import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

export const FONT_FILES = {
  Anton: "fonts/Anton-Regular.woff2",
  "Archivo Black": "fonts/ArchivoBlack-Regular.woff2",
} as const;
export type FontName = keyof typeof FONT_FILES;

const loaded = new Map<FontName, Promise<void>>();

/** Load a bundled font once (loadFont holds the render until it is ready). */
export const ensureFont = (family: FontName): Promise<void> => {
  let p = loaded.get(family);
  if (!p) {
    p = loadFont({ family, url: staticFile(FONT_FILES[family]), weight: "400", display: "block" }).then(() => undefined);
    loaded.set(family, p);
  }
  return p;
};
