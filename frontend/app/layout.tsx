import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Multibeam Converter — .all to LAS",
  description:
    "Upload a Kongsberg multibeam .all file and download a UTM-projected LAS point cloud.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
