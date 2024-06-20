from functools import lru_cache
import time
import strawberry
import requests
import decimal
import json
from typing import Any, NewType, List, TypeAlias
from strawberry.extensions import AddValidationRules
from graphql.validation import NoSchemaIntrospectionCustomRule
from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter
import os

#remember to set url
NODE_URL = os.getenv("NODE_URL")

JSON = strawberry.scalar(
    NewType("JSON", object),
    description="The `JSON` scalar type represents JSON values as specified by ECMA-404",
    serialize=lambda v: v,
    parse_value=lambda v: v,
)

SignedTransaction = strawberry.scalar(
    NewType("SignedTransaction", str),
    description="SignedTransaction",
    serialize=lambda v: v,
    parse_value=lambda v: v,
)

def get_ttl_hash(seconds=2):
    """Return the same value withing `seconds` time period"""
    return round(time.time() / seconds)

def get_node_info():
    res = requests.get(f"{NODE_URL}/info")
    if res.ok:
        return res.json()
    else:
        return None
    
def get_transaction_count_by_address(address: str):
    res = requests.post(f"{NODE_URL}/blockchain/transaction/byAddress?offset=0&limit=1",json=address)
    if res.ok:
        return res.json()
    else:
        return None
    
cached_confirmed_boxes = {}

@lru_cache()
def get_unspent_boxes_by_address(address: str, ttl_hash=None):
    del ttl_hash

    box_map = {}
    more_boxes = True
    offset = 0
    limit = 1000
    ergo_tree = ""

    while more_boxes:
        res = requests.post(f"{NODE_URL}/blockchain/box/unspent/byAddress?offset={offset}&limit={limit}&sortDirection=asc&includeUnconfirmed=true",json=address)
        if res.ok:
            boxes = res.json()
            more_boxes = len(boxes) == limit
            offset += limit
            for box in boxes:
                box_map[box["boxId"]] = box
                ergo_tree = box["ergoTree"]
        else:
            more_boxes = False

    if ergo_tree != "":
      res = requests.post(f"{NODE_URL}/transactions/unconfirmed/byErgoTree?limit=100&offset=0",json=ergo_tree)
      if res.ok:
          mempool_transactions = res.json()
          for mem_tx in mempool_transactions:
              for input in mem_tx["inputs"]:
                  if input["boxId"] in box_map:
                      del box_map[input["boxId"]]
                      print("spent box")

    return box_map

token_info_cache = {}

def get_token_info(token_id: str):
    if token_id in token_info_cache:
        return token_info_cache[token_id]
    res_token = requests.get(f"{NODE_URL}/blockchain/token/byId/{token_id}")
    if res_token.ok:
        token_json = res_token.json()
        res_box = requests.get(f"{NODE_URL}/blockchain/box/byId/{token_json['boxId']}")
        if res_box.ok:
          token_json["box"] = res_box.json()
          token_info_cache[token_id] = token_json
          return token_info_cache[token_id]
        else:
            return None
    else:
        return None
    
used_address_map = {}

def is_address_used(address: str):
    if address in used_address_map:
        return True
    res = requests.post(f"{NODE_URL}/blockchain/box/byAddress?offset=0&limit=1",json=address)
    if res.ok:
        if len(res.json()["items"]) > 0:
            used_address_map[address] = True
            return True
        else:
            return False
    else:
        False

def get_last_headers(count: int):
    res = requests.get(f"{NODE_URL}/blocks/lastHeaders/{count}")
    if res.ok:
        return res.json()
    else:
        return None
    
def is_tx_in_pool(transactionId: str):
    res = requests.head(f"{NODE_URL}/transactions/unconfirmed/{transactionId}")
    if res.ok:
        return True
    else:
        return False
    
def check_transaction(signed_tx: str) -> str:
    res = requests.post(f"{NODE_URL}/transactions/check", json=json.loads(signed_tx))
    if res.ok:
        return res.text
    else:
        print(res.text)
        return None
    
def submit_transaction(signed_tx: str) -> str:
    res = requests.post(f"{NODE_URL}/transactions", json=json.loads(signed_tx))
    if res.ok:
        return res.text
    else:
        print(res.text)
        return None

@strawberry.type
class Asset:
    amount: decimal.Decimal
    tokenId: str
    name: str
    decimals: int

@strawberry.type
class Balance:
    nanoErgs: decimal.Decimal
    assets: List[Asset]

@strawberry.type
class Address:
    address: str
    balance: Balance
    used: bool

@strawberry.type
class Info:
    version: str

@strawberry.type
class State:
    network: str

@strawberry.type
class BlockHeader:
    headerId: str
    parentId: str
    version: int
    height: int
    difficulty: decimal.Decimal
    adProofsRoot: str
    stateRoot: str
    transactionsRoot: str
    timestamp: decimal.Decimal
    nBits: decimal.Decimal
    extensionHash: str
    powSolutions: JSON
    votes: List[int]

@strawberry.type
class Box:
    boxId: str
    transactionId: str
    value: decimal.Decimal
    creationHeight: int
    index: int
    ergoTree: str
    additionalRegisters: JSON
    assets: List[Asset]

@strawberry.type
class TokenInfo:
    tokenId: str
    type: str
    emissionAmount: decimal.Decimal
    name: str
    description: str
    decimals: int
    boxId: str
    box: Box

def get_balance_from_boxes(boxes: dict) -> Balance:
    assets = {}
    nanoErgs = 0

    for box in boxes.values():
        nanoErgs += box["value"]
        for asset in box["assets"]:
            if asset["tokenId"] not in assets:
                assets[asset["tokenId"]] = 0
            assets[asset["tokenId"]] += asset["amount"]
        
    tokens = []

    for asset in assets:
        token_info = get_token_info(asset)
        tokens.append(Asset(amount=assets[asset], tokenId=asset, name=token_info["name"], decimals=token_info["decimals"]))

    return Balance(nanoErgs=nanoErgs,assets=tokens)

@strawberry.type
class Transaction:
    transactionId: str

@strawberry.type
class Mempool:
    
    @strawberry.field
    def boxes(self, address: str = "", skip: int = 0, take: int = 1) -> List[Box]:
        return []
    
    @strawberry.field
    def transactions(self, transactionId: str = "") -> List[Transaction]:
        if is_tx_in_pool(transactionId):
            return [Transaction(transactionId=transactionId)]
        else:
            return []
 
@strawberry.type
class Query:
    
    @strawberry.field
    def info(self) -> Info:
        return Info(version="0.5.1")
    
    @strawberry.field
    def state(self) -> State:
        node_info = get_node_info()
        return State(network=node_info["network"])
    
    @strawberry.field
    def addresses(self, info: strawberry.Info, addresses: List[str] = []) -> List[Address]:
        res = []
        balance_needed = False
        for field in info.selected_fields[0].selections:
            if field.name == "balance":
                balance_needed = True
        for address in addresses:
            balance = None
            if balance_needed:
              boxes = get_unspent_boxes_by_address(address,get_ttl_hash())
              balance = get_balance_from_boxes(boxes)
            used = is_address_used(address)
            res.append(Address(address=address, balance=balance, used=used))
        return res
    
    @strawberry.field
    def blockHeaders(self, take: int = 1) -> List[BlockHeader]:
        headers = get_last_headers(take)
        block_headers = []
        for header in headers:
            block_headers.append(
                BlockHeader(
                    headerId=header["id"],
                    parentId=header["parentId"],
                    version=header["version"],
                    height=header["height"],
                    difficulty=header["difficulty"],
                    adProofsRoot=header["adProofsRoot"],
                    stateRoot=header["stateRoot"],
                    transactionsRoot=header["transactionsRoot"],
                    timestamp=header["timestamp"],
                    nBits=header["nBits"],
                    extensionHash=header["extensionHash"],
                    powSolutions=header["powSolutions"],
                    votes=list(bytes.fromhex(header["votes"]))
                )
            )
        return block_headers
    
    @strawberry.field
    def boxes(self, addresses: List[str] = [], skip: int = 0, take: int = 1, spent: bool = False) -> List[Box]:
        box_list = []
        for address in addresses:
            box_map = get_unspent_boxes_by_address(address,get_ttl_hash())
            for boxId in box_map:
              assets = []
              for asset in box_map[boxId]["assets"]:
                  token_info = get_token_info(asset["tokenId"])
                  assets.append(Asset(amount=asset["amount"], tokenId=asset["tokenId"],name=token_info["name"],decimals=token_info["decimals"]))
              box_list.append(
                  Box(
                      boxId=boxId,
                      transactionId=box_map[boxId]["transactionId"],
                      value=box_map[boxId]["value"],
                      creationHeight=box_map[boxId]["creationHeight"],
                      index=box_map[boxId]["index"],
                      ergoTree=box_map[boxId]["ergoTree"],
                      additionalRegisters=box_map[boxId]["additionalRegisters"],
                      assets=assets
                  )
              )
        return box_list[skip:skip+take]
    
    @strawberry.field
    def tokens(self, tokenId: str = "") -> List[TokenInfo]:
        token_info = get_token_info(tokenId)
        assets = []
        for asset in token_info["box"]["assets"]:
            asset_info = get_token_info(asset["tokenId"])
            assets.append(Asset(amount=asset["amount"], tokenId=asset["tokenId"],name=asset_info["name"],decimals=asset_info["decimals"]))
        return [TokenInfo(
            tokenId=tokenId,
            type="EIP-004",
            emissionAmount=token_info["emissionAmount"],
            name=token_info["name"],
            description=token_info["description"],
            decimals=token_info["decimals"],
            boxId=token_info["boxId"],
            box=Box(
                boxId=token_info["box"]["boxId"],
                transactionId=token_info["box"]["transactionId"],
                value=token_info["box"]["value"],
                creationHeight=token_info["box"]["creationHeight"],
                index=token_info["box"]["index"],
                ergoTree=token_info["box"]["ergoTree"],
                additionalRegisters=token_info["box"]["additionalRegisters"],
                assets=assets
            )
        )]
    
@strawberry.type
class Mutation:
    
    @strawberry.field
    def checkTransaction(signedTransaction: str = "") -> str:
        return check_transaction(signedTransaction)
    
    @strawberry.field
    def submitTransaction(signedTransaction: str = "") -> str:
        return submit_transaction(signedTransaction)
    
schema = strawberry.Schema(query=Query,mutation=Mutation,extensions=[
        AddValidationRules([NoSchemaIntrospectionCustomRule]),
    ])

graphql_app = GraphQLRouter(schema,graphql_ide=None)

app = FastAPI()
app.include_router(graphql_app, prefix="/graphql")