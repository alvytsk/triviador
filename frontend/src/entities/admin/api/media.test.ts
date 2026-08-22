import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiFetchError } from "@/shared/api";
import { server } from "../../../../testing/msw";
import { uploadMedia } from "./media";

const SUMMARY = {
  id: "abc123",
  url: "https://media.example/ab/abc123.webp",
  width: 100,
  height: 100,
  byte_size: 4096,
};

describe("uploadMedia", () => {
  it("sends the file's bytes as the raw request body with its own content type, not a multipart form", async () => {
    let seenContentType: string | null = null;
    let seenBody: string | null = null;
    let seenIsMultipart = false;
    server.use(
      http.post("/api/admin/media", async ({ request }) => {
        seenContentType = request.headers.get("content-type");
        seenIsMultipart = seenContentType?.includes("multipart") ?? false;
        seenBody = await request.text();
        return HttpResponse.json(SUMMARY, { status: 201 });
      }),
    );
    const file = new File(["fake-image-bytes"], "photo.png", { type: "image/png" });
    await expect(uploadMedia(file)).resolves.toEqual(SUMMARY);
    expect(seenContentType).toBe("image/png");
    expect(seenIsMultipart).toBe(false);
    expect(seenBody).toBe("fake-image-bytes");
  });

  it("raises a media_rejected envelope as an envelope-kind ApiFetchError", async () => {
    server.use(
      http.post("/api/admin/media", () =>
        HttpResponse.json(
          { code: "media_rejected", message: "not an image", details: null },
          { status: 415 },
        ),
      ),
    );
    const file = new File(["not an image"], "x.txt", { type: "text/plain" });
    const error = await uploadMedia(file).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).kind).toBe("envelope");
    expect((error as ApiFetchError).code).toBe("media_rejected");
  });

  it("raises a transport ApiFetchError when the success body does not match the schema", async () => {
    server.use(
      http.post("/api/admin/media", () => HttpResponse.json({ id: "abc123" }, { status: 201 })),
    );
    const file = new File(["fake-image-bytes"], "photo.png", { type: "image/png" });
    const error = await uploadMedia(file).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).kind).toBe("transport");
  });
});
