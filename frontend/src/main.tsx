import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Providers } from "./app/app-providers";
import { createQueryClient } from "./app/query-client";
import { routeTree } from "./app/routes/routeTree.gen";
import "./styles.css";

const queryClient = createQueryClient();
const router = createRouter({ routeTree, context: { queryClient }, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const root = document.getElementById("root");
if (root === null) throw new Error("no #root");

createRoot(root).render(
  <StrictMode>
    <Providers queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);
