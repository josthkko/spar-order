"""SPAR Slovenia online store API client (Instaleap GraphQL backend).

Talks to the same endpoints the online.spar.si web app uses, so no browser
automation is needed. See API.md for what was discovered about the API.

Usage:
    client = SparClient()
    await client.login("email", "password")
    results = await client.search("piščančje prsi")
    await client.add_to_cart(results[0]["sku"])
    cart = await client.get_cart()
"""

import json
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

# Constants
# Both keys below are the public client keys embedded in the online.spar.si web app,
# not account secrets. Override via env if SPAR rotates them.
FIREBASE_API_KEY = os.environ.get("SPAR_FIREBASE_API_KEY", "AIzaSyCmfNNEuGo-cUWzi5R43h5B52CuRw-KckA")
FIREBASE_AUTH_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
INSTALEAP_ENDPOINT = "https://deadpool.unified-jennet.instaleap.io/api/v3"
CLIENT_ID = "SPAR_SLOVENIA"
STORE_REFERENCE = "81701"
DPL_API_KEY = os.environ.get("SPAR_DPL_API_KEY", "febe3691-edb4-498b-b1a6-d6fe1ddd068e")
CLIENT_VERSION = "0.18.508"

# GraphQL Queries
SEARCH_QUERY = """
query GetSearchSuggestion($getSearchSuggestionInput: GetSearchSuggestionInput!) {
  getSearchSuggestion(getSearchSuggestionInput: $getSearchSuggestionInput) {
    name
    sku
    ean
    isActive
    slug
    isAvailable
    photosUrl
    seals
  }
}
"""

ADD_TO_CART_MUTATION = """
fragment PricePerBaseUnit on ProductPrice {
  taxes totalBeforeTaxes subtotal fullPrice discount total
  taxesSummary { id name value type rate }
}
fragment CartProduct on CartProductModel {
  id name unitQuantity baseUnitPrice reference unit baseUnit
  price { ...PricePerBaseUnit }
  imageUrl minUnitQuantity maxUnitQuantity status
}
fragment CartPricesSummary on PricesSummary {
  shippingFee taxes discounts subtotal fullPrice total
  productsSummary {
    productsTotal fullProductsPrice totalProductsDiscount
    missingForMinimumOrderValue missingForFreeShipping
  }
}
mutation AddProductToEcommerceCart($addProductToCartInput: AddProductToCartInput!) {
  addProductToEcommerceCart(addProductToCartInput: $addProductToCartInput) {
    id
    products { ...CartProduct }
    pricesSummary { ...CartPricesSummary }
  }
}
"""

VALIDATE_CART_QUERY = """
fragment PricePerBaseUnit on ProductPrice {
  taxes totalBeforeTaxes subtotal fullPrice discount total
  taxesSummary { id name value type rate }
}
fragment ValidateCartProduct on ValidatedCartProductModel {
  id name unitQuantity baseUnitPrice reference unit baseUnit
  status imageUrl maxUnitQuantity minUnitQuantity slug
  price { ...PricePerBaseUnit }
  pricePerBaseUnit { ...PricePerBaseUnit }
  availability stockWarning
  categoriesData { reference name level categoryNamesPath }
}
fragment CartPricesSummary on PricesSummary {
  shippingFee taxes discounts subtotal fullPrice total
  productsSummary {
    productsTotal fullProductsPrice totalProductsDiscount
    missingForMinimumOrderValue missingForFreeShipping
  }
}
mutation ValidateEcommerceCart($cartId: ID!, $allowSplitOrders: Boolean) {
  validateEcommerceCart(cartId: $cartId, allowSplitOrders: $allowSplitOrders) {
    id operationalModel
    products { ...ValidateCartProduct }
    pricesSummary { ...CartPricesSummary }
  }
}
"""

REMOVE_PRODUCT_MUTATION = """
fragment PricePerBaseUnit on ProductPrice {
  taxes totalBeforeTaxes subtotal fullPrice discount total
}
fragment CartProduct on CartProductModel {
  id name unitQuantity baseUnitPrice reference unit
  price { ...PricePerBaseUnit }
  status
}
fragment CartPricesSummary on PricesSummary {
  taxes discounts subtotal fullPrice total
  productsSummary { productsTotal fullProductsPrice }
}
mutation deleteProductInEcommerceCart($deleteProductInCartInput: DeleteProductInCartInput!) {
  deleteProductInEcommerceCart(deleteProductInCartInput: $deleteProductInCartInput) {
    id
    products { ...CartProduct }
    pricesSummary { ...CartPricesSummary }
  }
}
"""

UPDATE_QUANTITY_MUTATION = """
fragment PricePerBaseUnit on ProductPrice {
  taxes totalBeforeTaxes subtotal fullPrice discount total
}
fragment CartProduct on CartProductModel {
  id name unitQuantity baseUnitPrice reference unit
  price { ...PricePerBaseUnit }
  status
}
fragment CartPricesSummary on PricesSummary {
  taxes discounts subtotal fullPrice total
  productsSummary { productsTotal fullProductsPrice }
}
mutation UpdateProductInEcommerceCart($updateProductInCartInput: UpdateProductInCartInput!) {
  updateProductInEcommerceCart(updateProductInCartInput: $updateProductInCartInput) {
    id
    products { ...CartProduct }
    pricesSummary { ...CartPricesSummary }
  }
}
"""

SEARCH_PRODUCTS_QUERY = """
query searchProducts($searchProductsInput: SearchProductsInput!) {
  searchProducts(searchProductsInput: $searchProductsInput) {
    pagination { total { value } pages page }
    products {
      name sku price pricePerSubUnit
      promotion { type isActive description endDateTime conditions { quantity price } }
      isAvailable isActive
      unit subUnit
      photosUrl brand slug ean
      seals stockWarning
      categories { name }
    }
  }
}
"""

GET_ACTIVE_CART_QUERY = """
query getActiveEcommerceCart($input: GetActiveEcommerceCartInput!) {
  getActiveEcommerceCart(getActiveCartInput: $input) {
    id operationalModel status
    address { id city zipCode state description latitude longitude }
    deliverySlot { id start end type prices { final initial } }
    products {
      id name unitQuantity baseUnitPrice reference unit
      price { total }
      status
    }
    pricesSummary {
      shippingFee taxes discounts subtotal fullPrice total
      productsSummary {
        productsTotal fullProductsPrice totalProductsDiscount
        missingForMinimumOrderValue missingForFreeShipping
      }
    }
  }
}
"""

GET_CART_QUERY = """
query getEcommerceCart($cartId: ID!) {
  getEcommerceCart(cartId: $cartId) {
    id operationalModel status
    store { id name reference }
    address { id city zipCode state description latitude longitude }
    deliverySlot { id start end type prices { final initial } }
    products {
      id name unitQuantity baseUnitPrice reference unit
      price { total }
      status
    }
    pricesSummary {
      shippingFee taxes discounts subtotal fullPrice total
      productsSummary {
        productsTotal fullProductsPrice totalProductsDiscount
        missingForMinimumOrderValue missingForFreeShipping
      }
    }
  }
}
"""

VALIDATE_CART_WITH_GROUPS_QUERY = """
mutation ValidateEcommerceCartWithGroups($cartId: ID!, $allowSplitOrders: Boolean) {
  validateEcommerceCart(cartId: $cartId, allowSplitOrders: $allowSplitOrders) {
    id operationalModel
    groups { id storeReference store { id name reference } }
    products {
      id name unitQuantity availability
      price { total }
    }
    pricesSummary {
      shippingFee taxes discounts subtotal fullPrice total
      productsSummary {
        productsTotal fullProductsPrice totalProductsDiscount
        missingForMinimumOrderValue missingForFreeShipping
      }
    }
  }
}
"""

GET_DELIVERY_SLOTS_QUERY = """
query getDeliverySlots($input: GetDeliverySlotsInput!) {
  getDeliverySlots(getDeliverySlotsInput: $input) {
    id start end type
    prices { final initial }
  }
}
"""

GET_ADDRESSES_QUERY = """
query getAddresses($input: GetAddressesInput!) {
  getAddresses(getAddressesInput: $input) {
    id city zipCode state description latitude longitude
    address { other }
  }
}
"""

UPDATE_CART_MUTATION = """
mutation updateEcommerceCart($input: UpdateEcommerceCartInput!) {
  updateEcommerceCart(updateCartInput: $input) {
    id operationalModel status
    address { id city zipCode state description }
    deliverySlot { id start end }
    paymentConfigurationId
  }
}
"""

PURCHASE_CART_MUTATION = """
mutation purchaseEcommerceCart($input: PurchaseCartInput!) {
  purchaseEcommerceCart(purchaseCartInput: $input) {
    id status jobNumber jobExtenalId jobId
  }
}
"""

CREATE_CART_MUTATION = """
mutation createEcommerceCart($input: CreateEcommerceCartInput!) {
  createEcommerceCart(createCartInput: $input) {
    id operationalModel
  }
}
"""

GET_CART_PAYMENT_METHODS_QUERY = """
query getEcommerceCartPaymentMethods($cartId: ID!) {
  getEcommerceCartPaymentMethods(cartId: $cartId) {
    names type paymentConfigurationId
  }
}
"""

GET_ORDERS_QUERY = """
query GetOrdersByCustomer($limit: Int, $page: Int) {
  getOrdersByCustomer(limit: $limit, page: $page) {
    id status operationalModel jobId jobNumber
    products {
      id reference price quantity name imageUrl
      baseUnit baseUnitQuantity variant instruction format type
    }
    deliverySlot { from to }
    address { id description city }
  }
}
"""

ADD_PRODUCTS_TO_CART_MUTATION = """
mutation AddProductsToEcommerceCart($addProductsToCartInput: AddProductsToCartInput!) {
  addProductsToEcommerceCart(addProductsToCartInput: $addProductsToCartInput) {
    cart {
      id status
      products { id name unitQuantity reference unit price { total } }
      pricesSummary { subtotal shippingFee total }
    }
  }
}
"""


class SparClient:
    """Async client for SPAR Slovenia online store API."""

    def __init__(self):
        self.token: str | None = None
        self.cart_id: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "dpl-api-key": DPL_API_KEY,
            "client-name": f"e-commerce Moira Engine {CLIENT_ID}",
            "client-version": CLIENT_VERSION,
            "apollographql-client-name": f"e-commerce Moira Engine client {CLIENT_ID}",
            "apollographql-client-version": CLIENT_VERSION,
        }
        if self.token:
            h["token"] = self.token
        return h

    async def _graphql(self, operation_name: str, query: str, variables: dict) -> dict:
        """Execute a GraphQL operation against the Instaleap API."""
        session = await self._get_session()
        payload = [{"operationName": operation_name, "variables": variables, "query": query}]
        async with session.post(INSTALEAP_ENDPOINT, json=payload, headers=self._headers()) as resp:
            data = await resp.json()
            if isinstance(data, list):
                data = data[0]
            if "errors" in data:
                raise Exception(f"GraphQL error: {data['errors']}")
            return data["data"]

    async def login(self, email: str, password: str) -> str:
        """Login via Firebase Auth. Returns JWT token."""
        session = await self._get_session()
        firebase_headers = {
            "Content-Type": "application/json",
            "Referer": "https://online.spar.si/",
        }
        async with session.post(FIREBASE_AUTH_URL, json={
            "email": email,
            "password": password,
            "returnSecureToken": True,
        }, headers=firebase_headers) as resp:
            data = await resp.json()
            if "error" in data:
                raise Exception(f"Login failed: {data['error']['message']}")
            firebase_token = data["idToken"]

        # Exchange Firebase token for Instaleap JWT via v2 signIn mutation
        async with session.post(
            "https://deadpool.unified-jennet.instaleap.io/api/v2",
            json=[{
                "operationName": "signIn",
                "variables": {
                    "accessToken": firebase_token,
                    "clientId": CLIENT_ID,
                },
                "query": """mutation signIn($accessToken: String!, $clientId: String!) {
                    signIn(accessToken: $accessToken, clientId: $clientId) {
                        token customer { id name email }
                    }
                }"""
            }],
            headers=self._headers(),
        ) as resp:
            data = await resp.json()
            if isinstance(data, list):
                data = data[0]
            if "errors" in data:
                raise Exception(f"Instaleap login failed: {data['errors']}")
            result = data["data"]["signIn"]
            self.token = result["token"]
            logger.info(f"Logged in as {result['customer']['name']}")
            return self.token

    async def search(self, query: str) -> list[dict]:
        """Search for products. Returns list of product dicts with name, sku, isAvailable."""
        data = await self._graphql("GetSearchSuggestion", SEARCH_QUERY, {
            "getSearchSuggestionInput": {
                "clientId": CLIENT_ID,
                "partialQuery": query,
                "storeReference": STORE_REFERENCE,
            }
        })
        return data["getSearchSuggestion"]

    async def search_full(self, query: str, page: int = 1, page_size: int = 20) -> dict:
        """Full product search with prices, pagination, and availability.

        Returns dict with 'products' list and 'pagination' info.
        Each product includes: name, sku, price, promotion, pricePerSubUnit,
        isAvailable, isActive, unit, subUnit, brand, categories, photosUrl, etc.
        """
        data = await self._graphql("searchProducts", SEARCH_PRODUCTS_QUERY, {
            "searchProductsInput": {
                "clientId": CLIENT_ID,
                "storeReference": STORE_REFERENCE,
                "pageSize": page_size,
                "currentPage": page,
                "search": [{"query": query}],
            }
        })
        return data["searchProducts"]

    async def add_to_cart(self, sku: str, quantity: int = 1, unit: str | None = None) -> dict:
        """Add a product to cart by SKU. Returns updated cart.

        Args:
            sku: Product SKU/reference
            quantity: Number of units to add
            unit: Product unit (KOS or KG). If not provided, auto-detects
                  by searching for the product. Weight-based products (e.g.
                  bananas) use KG; packaged products use KOS.
        """
        if not self.cart_id:
            raise Exception("No cart_id set. Call get_cart() or set cart_id first.")
        if not unit:
            # Auto-detect unit from product data via search_full
            try:
                full = await self.search_full(sku, page_size=10)
                for p in full.get("products", []):
                    if str(p.get("sku")) == str(sku):
                        unit = p.get("unit", "KOS")
                        break
            except Exception:
                pass
            if not unit:
                unit = "KOS"
        data = await self._graphql("AddProductToEcommerceCart", ADD_TO_CART_MUTATION, {
            "addProductToCartInput": {
                "cartId": self.cart_id,
                "reference": sku,
                "unit": unit,
                "unitQuantity": quantity,
            }
        })
        return data["addProductToEcommerceCart"]

    async def remove_from_cart(self, product_id: str) -> dict:
        """Remove a product from cart by product ID (not SKU)."""
        if not self.cart_id:
            raise Exception("No cart_id set.")
        data = await self._graphql("deleteProductInEcommerceCart", REMOVE_PRODUCT_MUTATION, {
            "deleteProductInCartInput": {
                "cartId": self.cart_id,
                "productId": product_id,
            }
        })
        return data["deleteProductInEcommerceCart"]

    async def update_quantity(self, product_id: str, quantity: int) -> dict:
        """Update product quantity in cart."""
        if not self.cart_id:
            raise Exception("No cart_id set.")
        data = await self._graphql("UpdateProductInEcommerceCart", UPDATE_QUANTITY_MUTATION, {
            "updateProductInCartInput": {
                "cartId": self.cart_id,
                "productId": product_id,
                "unitQuantity": quantity,
            }
        })
        return data["updateProductInEcommerceCart"]

    async def validate_cart(self) -> dict:
        """Validate cart - returns full cart with prices and availability."""
        if not self.cart_id:
            raise Exception("No cart_id set.")
        data = await self._graphql("ValidateEcommerceCart", VALIDATE_CART_QUERY, {
            "cartId": self.cart_id,
            "allowSplitOrders": True,
        })
        return data["validateEcommerceCart"]

    async def get_cart_summary(self) -> str:
        """Get a human-readable cart summary."""
        cart = await self.validate_cart()
        lines = ["Cart contents:"]
        for p in cart["products"]:
            avail = "available" if p["availability"] else "UNAVAILABLE"
            price = p["price"]["total"]
            lines.append(f"  {p['unitQuantity']}x {p['name']} - {price:.2f} EUR ({avail})")
        summary = cart["pricesSummary"]
        lines.append(f"\nTotal: {summary['total']:.2f} EUR")
        if summary["productsSummary"]["missingForMinimumOrderValue"] > 0:
            lines.append(f"Missing for minimum order: {summary['productsSummary']['missingForMinimumOrderValue']:.2f} EUR")
        return "\n".join(lines)

    async def get_active_cart(self, operational_model: str = "DELIVERY") -> dict:
        """Get the active cart for given operational model, or create one.

        Sets self.cart_id as a side effect.
        Returns the cart dict with id, products, pricesSummary, etc.
        """
        try:
            data = await self._graphql("getActiveEcommerceCart", GET_ACTIVE_CART_QUERY, {
                "input": {
                    "storeReference": STORE_REFERENCE,
                    "operationalModel": operational_model,
                }
            })
            cart = data["getActiveEcommerceCart"]
            self.cart_id = cart["id"]
            return cart
        except Exception as e:
            if "NOT_FOUND" in str(e) or "null" in str(e):
                # No active cart, create one
                return await self.create_cart(operational_model)
            raise

    async def get_cart(self) -> dict:
        """Get cart by ID, or fetch active delivery cart.

        Sets self.cart_id as a side effect.
        Returns the cart dict.
        """
        if self.cart_id:
            data = await self._graphql("getEcommerceCart", GET_CART_QUERY, {
                "cartId": self.cart_id,
            })
            return data["getEcommerceCart"]
        return await self.get_active_cart()

    async def create_cart(self, operational_model: str = "PICK_UP") -> dict:
        """Create a new cart. Note: DELIVERY may fail, use PICK_UP then update.

        Returns cart dict with id and operationalModel.
        """
        data = await self._graphql("createEcommerceCart", CREATE_CART_MUTATION, {
            "input": {
                "storeReference": STORE_REFERENCE,
                "operationalModel": operational_model,
            }
        })
        cart = data["createEcommerceCart"]
        self.cart_id = cart["id"]
        return cart

    async def validate_cart_with_groups(self) -> dict:
        """Validate cart and return groups (needed for delivery slots).

        Returns validated cart with groups[].id for use with get_delivery_slots.
        """
        if not self.cart_id:
            raise Exception("No cart_id set.")
        data = await self._graphql("ValidateEcommerceCartWithGroups", VALIDATE_CART_WITH_GROUPS_QUERY, {
            "cartId": self.cart_id,
            "allowSplitOrders": True,
        })
        return data["validateEcommerceCart"]

    async def get_delivery_slots(self, start_date: str, end_date: str | None = None, group_id: str | None = None) -> list[dict]:
        """Get delivery time slots for a date range.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD), defaults to start_date + 1 day
            group_id: Cart group ID from validate_cart_with_groups().
                      If not provided, validates cart to get it.

        Returns list of slot dicts with id (JWT), start, end, type, prices.
        The slot id contains from/to/expiresAt needed for updateEcommerceCart.
        """
        if not self.cart_id:
            raise Exception("No cart_id set.")

        if not group_id:
            validated = await self.validate_cart_with_groups()
            groups = validated.get("groups", [])
            if not groups:
                raise Exception("No groups found in cart. Cart may be empty.")
            group_id = groups[0]["id"]

        if not end_date:
            from datetime import datetime, timedelta
            d = datetime.strptime(start_date, "%Y-%m-%d")
            end_date = (d + timedelta(days=1)).strftime("%Y-%m-%d")

        data = await self._graphql("getDeliverySlots", GET_DELIVERY_SLOTS_QUERY, {
            "input": {
                "cartId": self.cart_id,
                "start": start_date,
                "end": end_date,
                "groupId": group_id,
            }
        })
        return data["getDeliverySlots"]

    async def get_addresses(self) -> list[dict]:
        """Get saved delivery addresses.

        Returns list of address dicts with id, city, zipCode, state,
        description, latitude, longitude, address.other (formatted string).
        """
        data = await self._graphql("getAddresses", GET_ADDRESSES_QUERY, {
            "input": {
                "storeReference": STORE_REFERENCE,
            }
        })
        return data["getAddresses"]

    async def update_cart(self, address_id: str | None = None, slot: dict | None = None, group_id: str | None = None,
                          payment_configuration_id: str | None = None, payment_method: dict | None = None,
                          online_payment_params: dict | None = None) -> dict:
        """Update cart with delivery address, time slot, and/or payment configuration.

        Args:
            address_id: Address ID from get_addresses()
            slot: SlotInput dict with {id, from, to, expiresAt, type}.
            group_id: Group ID from validate_cart_with_groups(). Required when setting slot.
            payment_configuration_id: Payment config ID from getEcommerceCartPaymentMethods.
            payment_method: Dict with {type, name} for PAY_ON_DELIVERY etc.
            online_payment_params: Dict with {cardExternalId, currency} for initial payment setup.

        Returns updated cart dict (includes paymentConfigurationId).
        """
        if not self.cart_id:
            raise Exception("No cart_id set.")
        input_data = {"cartId": self.cart_id}
        if address_id:
            input_data["addressId"] = address_id
        if group_id:
            input_data["groupId"] = group_id
        if slot:
            input_data["slot"] = slot
        if payment_configuration_id:
            input_data["paymentConfigurationId"] = payment_configuration_id
        if payment_method:
            input_data["paymentMethod"] = payment_method
        if online_payment_params:
            input_data["onlinePaymentParams"] = online_payment_params
        data = await self._graphql("updateEcommerceCart", UPDATE_CART_MUTATION, {
            "input": input_data,
        })
        return data["updateEcommerceCart"]

    async def purchase_cart(self, payment_method_type: str = "PAY_ON_DELIVERY", payment_method_name: str = "Plačilo ob dostavi") -> dict:
        """Purchase/checkout the cart with cash on delivery.

        Args:
            payment_method_type: PaymentMethodType enum value (e.g. PAY_ON_DELIVERY)
            payment_method_name: Display name for payment method

        Returns purchase result with id, status, jobNumber, jobExtenalId, jobId.
        """
        if not self.cart_id:
            raise Exception("No cart_id set.")
        data = await self._graphql("purchaseEcommerceCart", PURCHASE_CART_MUTATION, {
            "input": {
                "cartId": self.cart_id,
                "paymentInput": {
                    "paymentMethod": {
                        "type": payment_method_type,
                        "name": payment_method_name,
                    }
                },
            }
        })
        return data["purchaseEcommerceCart"]

    async def get_cart_payment_methods(self) -> list[dict]:
        """Get available payment methods for the current cart.

        Returns list of dicts with 'names' and 'type' fields.
        Payment types: PAY_ON_DELIVERY, PREPAID
        Payment names: CASH, PAYMENT_LINK, PAYMENT_TERMINAL, CREDIT_CARD
        """
        if not self.cart_id:
            raise Exception("No cart_id set.")
        data = await self._graphql("getEcommerceCartPaymentMethods", GET_CART_PAYMENT_METHODS_QUERY, {
            "cartId": self.cart_id,
        })
        return data["getEcommerceCartPaymentMethods"]

    async def get_orders(self, limit: int | None = None, page: int | None = None) -> list[dict]:
        """Get past orders for the current customer.

        Returns list of orders with products, delivery slot, address, status.
        """
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page
        data = await self._graphql("GetOrdersByCustomer", GET_ORDERS_QUERY, variables)
        return data["getOrdersByCustomer"]

    async def add_products_to_cart(self, products: list[dict]) -> dict:
        """Bulk add products to cart.

        Args:
            products: List of dicts with keys: reference, unit, unitQuantity.
                      Optional: variant, instruction.

        Returns the updated cart dict.
        """
        if not self.cart_id:
            raise Exception("No cart_id set.")
        items = []
        for p in products:
            items.append({
                "reference": p["reference"],
                "variant": p.get("variant"),
                "unit": p.get("unit", "KOS"),
                "unitQuantity": p.get("unitQuantity", 1),
                "instruction": p.get("instruction", ""),
            })
        data = await self._graphql("AddProductsToEcommerceCart", ADD_PRODUCTS_TO_CART_MUTATION, {
            "addProductsToCartInput": {
                "cartId": self.cart_id,
                "products": items,
            }
        })
        return data["addProductsToEcommerceCart"]["cart"]

    @staticmethod
    def decode_slot_jwt(slot_id: str) -> dict:
        """Decode a delivery slot JWT to extract from/to/expiresAt.

        Returns dict with 'from', 'to', 'expiresAt' fields needed for SlotInput.
        """
        import base64
        payload = slot_id.split('.')[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        return json.loads(base64.urlsafe_b64decode(payload))

    def slot_to_input(self, slot_id: str, slot_type: str = "STATIC") -> dict:
        """Convert a delivery slot JWT ID to SlotInput for update_cart.

        Args:
            slot_id: JWT string from get_delivery_slots()[n]['id']
            slot_type: Slot type from get_delivery_slots()[n]['type'] (default: STATIC)

        Returns SlotInput dict: {id, from, to, expiresAt, type}
        """
        decoded = self.decode_slot_jwt(slot_id)
        return {
            "id": slot_id,
            "from": decoded["from"],
            "to": decoded["to"],
            "expiresAt": decoded["expiresAt"],
            "type": slot_type,
        }
