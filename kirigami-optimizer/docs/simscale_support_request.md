# Draft: message to SimScale support (send from your own SimScale account)

**Subject:** Enabling the CAD feature / query API for API-key access (External flow volume, Delete bodies)

Hello,

I run automated CFD parameter studies through the public SimScale API (Python SDK, `api.simscale.com/v0`): geometry import,
meshing, incompressible external-flow simulations and results download all work well.

The one step I still have to do by hand in the web app is the CAD-mode **External flow volume** followed by **Delete bodies**
on each imported geometry, once per design. I would like to run hundreds of designs unattended.

The public OpenAPI spec (`https://api.simscale.com/v0/openapi.json`) already defines the schemas `Cad`, `CadState`,
`CadImportRequest`, `CadQueryRequest` ("replaced at runtime ... fetched from cad session manager") and `CadFeatureRequest`
/ `CadFeatureResponse`, but it lists no paths that use them, and I could not find a CAD API class in the Python SDK.

Questions:
1. Is the CAD session / feature API available to API keys, and can it be enabled for my account?
2. If so, what are the endpoints and the feature names and parameters for "External flow volume" (custom box) and "Delete bodies"?
3. Alternatively, is there another supported way to create a flow volume from an imported solid via the API, so that the result is a single flow region
   (the canopy being a faceted, imported solid with a few hundred faces)?

Thank you,
<your name>
