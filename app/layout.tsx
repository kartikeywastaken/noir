import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NOIR — Authorized APK Workspace",
  description: "Plan, review, rebuild, sign, and verify authorized APK changes with exact control.",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">{children}</body>
    </html>
  );
}
