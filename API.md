# SPAR Slovenia API notes

What `spar_api.py` knows about the shop's backend (Instaleap, GraphQL). Useful
when something breaks or you want to add a command.

- **Endpoints**: `https://deadpool.unified-jennet.instaleap.io/api/v3`
  (everything), `/api/v2` (the `signIn` exchange only). Requests are batched:
  a JSON list with one `{operationName, variables, query}`.
- **Auth**: Firebase email/password sign-in (`identitytoolkit`,
  `Referer: https://online.spar.si/` is required) → `signIn(accessToken,
  clientId)` → Instaleap JWT in the `token` header. Lives about an hour.
- **Introspection is disabled.** To discover fields, send an invalid one and
  read the "Did you mean …" hint in the error.
- **Headers**: `dpl-api-key`, `client-name`, `client-version`,
  `apollographql-client-*` copied from the web app. The API key and the
  Firebase key are the web app's public client keys, not secrets.
- **Carts**: one active `DELIVERY` cart per account — `getActiveEcommerceCart`.
  `createEcommerceCart` only works for `PICK_UP`.
- **Products**: `searchProducts` returns price, stock and `promotion`.
  `previousPrice` is a dead field; a discount is active when
  `promotion.isActive` and `promotion.conditions[0].price < price`
  (`description` is "Takojšnji popust" or "Multipak"). Weight-priced items
  (`baseUnit: KG`) only accept whole-kg quantities.
- **Cart mutations**: `AddProductToEcommerceCart`, bulk
  `AddProductsToEcommerceCart`, `updateProductInEcommerceCart` (quantity;
  adding an existing line errors with `PRODUCT_IN_CART_ALREADY_EXISTS`),
  `removeProductFromEcommerceCart`.
- **Order lines** carry a `type`: `DEFAULT` products, `BAG`/`PACKAGE`
  server-managed packaging, `SAMPLE` free samples. Filter the last three when
  reordering.
- **Slots**: `validateEcommerceCart` → `groups[0].id` →
  `getDeliverySlots(cartId, start, end, groupId)`. Slot ids are JWTs carrying
  `from`, `to`, `expiresAt`. To set one: `updateEcommerceCart` with
  `slot: {id, from, to, expiresAt, type: "STATIC"}` (`type` is required) and
  the `groupId`.
- **Checkout**: the cart must carry a `paymentConfigurationId` or the purchase
  fails with `PAYMENT_METHOD_NOT_PROVIDED` or yields a broken order. Flow:
  `getEcommerceCartPaymentMethods(cartId)` (needs a non-empty cart) →
  `updateEcommerceCart({paymentConfigurationId})` → `purchaseEcommerceCart`.
  Methods seen: `PAY_ON_DELIVERY/{CASH, PAYMENT_LINK, PAYMENT_TERMINAL}`,
  `PREPAID/CREDIT_CARD`. Prepaid needs a 3-D Secure browser redirect and
  cannot complete from the API. A good purchase result has a `jobId`; a broken
  one has `jobId: null` and shows "not found" on the live tracker.
- **Orders**: `GetOrdersByCustomer` with products, `deliverySlot.from/to`,
  `status` (`FINISHED`, `CANCELLED`, …).
