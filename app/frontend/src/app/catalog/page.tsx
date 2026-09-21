"use client";
// Deep-link entry: /catalog.html?domain=<id> renders the catalog browser for that domain.
// Defaults to cyber-security-risk. The in-app "Catalog Browser" view (page.tsx) reuses the
// same <CatalogBrowser> component with a domain picker.
import { useEffect, useState } from "react";
import { CatalogBrowser } from "@/components/catalog-browser";

export default function CatalogPage() {
  const [domainId, setDomainId] = useState("cyber-security-risk");
  useEffect(() => {
    const d = new URLSearchParams(window.location.search).get("domain");
    if (d) setDomainId(d);
  }, []);
  return <CatalogBrowser domainId={domainId} />;
}
