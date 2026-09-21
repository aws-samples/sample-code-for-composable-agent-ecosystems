import "./globals.css";
import type { Metadata } from "next";
import { AuthProvider } from "@/components/auth-provider";
import { ViewModeProvider } from "@/components/view-mode";

export const metadata: Metadata = {
  title: "Tessera",
  description: "Composable agentic security platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <AuthProvider>
          <ViewModeProvider>{children}</ViewModeProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
