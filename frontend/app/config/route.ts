// Runtime configuration handed to the browser.
//
// NEXT_PUBLIC_* variables are inlined at build time, which would bake a
// hostname into the image. This route is evaluated per request instead, so
// the same image works wherever it is deployed.
export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json({
    // Where the browser should send bulk traffic (upload, download).
    // Empty string means "same origin", i.e. through the Next proxy.
    apiBase: process.env.PUBLIC_API_BASE ?? "",
  });
}
