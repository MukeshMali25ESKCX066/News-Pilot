import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NewsPilot AI",
  description: "News collection and article drafting workspace",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
