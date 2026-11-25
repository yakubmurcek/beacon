import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "../../../../web/tokens.css";
import "../../../../web/app.css";

export const metadata: Metadata = { title: "Beacon" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
