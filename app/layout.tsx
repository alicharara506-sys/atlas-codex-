import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "Atlas Quant V4 — Institutional Signal Intelligence",
  description: "Local-first, read-only MT5 quantitative signal decision terminal",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
