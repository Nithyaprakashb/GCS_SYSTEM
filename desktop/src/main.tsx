import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/manrope";
import "@fontsource-variable/jetbrains-mono";
import "maplibre-gl/dist/maplibre-gl.css";
import "./index.css";
import { App } from "./App";
import { GcsProvider } from "./state/GcsContext";

const container = document.getElementById("root");
if (!container) throw new Error("Root element missing from index.html");

createRoot(container).render(
  <StrictMode>
    <GcsProvider>
      <App />
    </GcsProvider>
  </StrictMode>,
);
