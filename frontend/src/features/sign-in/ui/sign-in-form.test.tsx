import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { meKey } from "@/entities/game";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { SignInForm } from "./sign-in-form";

const ME = { user_id: "u1", username: "alexey", display_name: "Alexey", role: "player" };

describe("SignInForm", () => {
  it("posts the two fields and puts the user in the cache", async () => {
    let body: unknown = null;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(ME);
      }),
    );
    const onDone = vi.fn();
    const harness = renderWithApp(<SignInForm onDone={onDone} />);
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "hunter2hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(body).toEqual({ username: "alexey", password: "hunter2hunter2" });
    expect(harness.queryClient.getQueryData(meKey())).toEqual(ME);
  });

  it("shows the server's message and code when the credentials are refused", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json(
          { code: "credentials_invalid", message: "invalid username or password", details: null },
          { status: 401 },
        ),
      ),
    );
    renderWithApp(<SignInForm onDone={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("status")).toHaveTextContent("invalid username or password");
    expect(screen.getByRole("status")).toHaveTextContent("credentials_invalid");
  });

  it("says the server could not be reached rather than inventing a code", async () => {
    server.use(http.post("/api/auth/login", () => HttpResponse.error()));
    renderWithApp(<SignInForm onDone={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "hunter2hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("could not be reached");
    expect(banner.textContent).not.toMatch(/credentials_invalid|unauthenticated/);
  });

  it("does not submit an empty form", async () => {
    const calls = vi.fn();
    server.use(
      http.post("/api/auth/login", () => {
        calls();
        return HttpResponse.json(ME);
      }),
    );
    renderWithApp(<SignInForm onDone={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(calls).not.toHaveBeenCalled();
  });
});
