from __future__ import print_function

import click
import json
import time
import binascii
import random
import string
from ethereum.utils import scan_bin, sha3, decode_int256, zpad, int_to_big_endian, keccak

from ion.args import arg_bytes20, arg_ethrpc
from ion.merkle import merkle_tree, merkle_hash, merkle_path
from ion.utils import u256be

on_transfer_signature = keccak.new(digest_bits=256).update('IonTransfer(address,address,uint256,bytes32,bytes)').hexdigest()

event_signatures = [on_transfer_signature]

def random_string(N):
    """
    Returns a random string to hash as pseudo data...
    """
    return ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.digits) for _ in range(N))


def jsonPrint(message, snippet):
    """
    Prints a message with a formatted slice of json which doesn't make your eyes bleed
    """
    print(message)
    print(json.dumps(snippet, indent=4, sort_keys=True))


def pack_txn(block_no, tx):
    """
    Packs all the information about a transaction into a deterministic fixed-sized array of bytes

        block_no || from || to || value || sha3(input)

    Where `value` is expanded to a 256bit big endian integer.
    """
    tx_from, tx_to, tx_value, tx_input = [scan_bin(x + ('0' * (len(x) % 2))) for x in [tx['from'], tx['to'], tx['value'], tx['input']]]
    tx_value = decode_int256(tx_value)

    return ''.join([
        tx_from,
        tx_to
    ])


def pack_log(block_no, tx, log):
    """
    Packs a log entry into one or more entries.

        block_no || address || topic || sha3(data)

    Where topic is the SHA3 of the event signature, e.g. `OnDeposit(bytes32)`
    """

    return ''.join([
        scan_bin(tx['from']),
        scan_bin(tx['to']),
        scan_bin(log['address']),
        scan_bin(log['topics'][1]),
        scan_bin(log['topics'][2]),
    ])

def pack_items(items):
    """
    Ensures items has minimum of 4 leaves.
    """
    start = len(items)
    if start < 4:
        for val in range(start, 4):
            new_item = random_string(16)
            items.append(sha3(new_item))
    else:
        pass

def processProof(item, item_tree, rpc):
    file = open("./data/merklePath" + str(rpc.port) + ".txt", "w")
    path = merkle_path(item, item_tree)
    # path = [str(hex(item) )[:-1] for item in path]
    for val in path:
        print("Path: ", hex(val))

    # file.write(path)
    for item in path:
        file.write("%s\n" % item)
    # for i in range(0, len(path)):
    #     var = path[i]
    #     var = str(hex(var))[:-1]
    #     print(var)
    #     file.write(var)

    file.close()

def processReference(ref, rpc):
    file = open("./data/reference" + str(rpc.port) + ".txt", "w")
    var = ref
    # var = str(var)[:-1]
    var = str(var)
    # print("reference: ", var)
    file.write(var)
    file.close()

def processLatestBlock(block, rpc):
    file = open("./data/latestBlock" + str(rpc.port) + ".txt", "w")
    # print("Latest block: ", block)
    # var = str(hex(block))[:-1]
    # print("Latest block: ", var)
    file.write(str(block))
    file.close()


def iter_blocks(rpc, start=1, group=1, backlog=0, interval=1):
    """Iterate through the block numbers"""
    obh = min(start, max(1, rpc.eth_blockNumber() - backlog))
    print("Starting block header: ", start)
    print("Previous block header: ", obh)
    obh -= obh % group
    blocks = []
    is_latest = False
    # Infinite loop event listener...
    while True:
        bh = rpc.eth_blockNumber() + 1
        for i in range(obh, bh):
            # XXX TODO: I think this is why the latest block info is not always in sync with geth
            if i == (bh - 1):
                is_latest = True
            blocks.append(i)
            if len(blocks) % group == 0:
                yield is_latest, blocks
                blocks = []
                is_latest = False
        obh = bh
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("Stopped by Keyboard interrupt")
            raise StopIteration

def lithium_process_ionlock_transfer_event(log, log_count, block_height, items):
    """Processes the ionlock transfer"""
    pass

def lithium_process_block(rpc, block_height, transfers):
    """Returns all items within the block"""
    block = rpc.eth_getBlockByNumber(block_height, False)
    items = []
    log_count = 0
    tx_count = 0
    if len(block['transactions']):
        for tx_hash in block['transactions']:
            tx = rpc.eth_getTransactionByHash(tx_hash)
            if tx['to'] is None:
                continue

            tx_count += 1
            packed_txns = pack_txn(block_height, tx)
            item_value = packed_txns
            # item_value = tx_count
            receipt = rpc.eth_getTransactionReceipt(tx_hash)
            transfer = False
            if len(receipt['logs']):
                # Note: this is where the juicy transfer information is found
                # TODO: should probably move a little more of this into lithium_process_ionlock_transfer_event
                for log_entry in receipt['logs']:
                    if log_entry['topics'][0][2:] in event_signatures:
                        print("Processing IonLock Transfer Event")

                        # Get the hashed reference
                        print("Reference: ", log_entry['topics'][2])
                        processReference(log_entry['topics'][2], rpc)
                        log_items = pack_log(block_height, tx, log_entry)

                        log_items = pack_log(block_height, tx, log_entry)
                        item_value = log_items
                        log_count += 1
                        transfer = True

            # XXX: This appends the flag regarding if an item is a IonTranfer or not...
            transfers.append(transfer)
            items.append(item_value)

    return items, tx_count, log_count


def lithium_process_block_group(rpc, block_group):
    """Process a group of blocks, returning the packed events and transactions"""
    print("Processing block group")
    items = []
    transfers = []
    group_tx_count = 0
    group_log_count = 0
    for block_height in block_group:
        block_items, tx_count, log_count = lithium_process_block(rpc, block_height, transfers)
        items += block_items
        group_tx_count += tx_count
        group_log_count += log_count

    return items, group_tx_count, group_log_count, transfers


def lithium_submit(batch, prev_root, rpc, link, account):
    """Submit batch of merkle roots to Beryllium"""
    ionlink = rpc.proxy("abi/IonLink.abi", link, account)
    if not len(batch):
        return False

    # XXX TODO: Using current strawman we submit update to the IonLock on chain B directly
    # however this needs to change in future which I am happy to explain this rationale: fmg
    current_block = batch[0][0]
    for pair in batch:
        if pair[2]:
            current_root = pair[1]
            time.sleep(5)
            ionlink.Update([prev_root, current_root])
            processLatestBlock(ionlink.GetLatestBlock(), rpc)
            prev_root = current_root

    return prev_root


@click.command(help="Ethereum event merkle tree relay daemon")
@click.option('--rpc-from', callback=arg_ethrpc, metavar="ip:port", default='127.0.0.1:8545', help="Source Ethereum JSON-RPC server")
@click.option('--rpc-to', callback=arg_ethrpc, metavar="ip:port", default='127.0.0.1:8545', help="Destination, where contract is")
@click.option('--from-account', callback=arg_bytes20, metavar="0x...20", required=True, help="Pays for Gas to fetch latest IonLock Block")
@click.option('--to-account', callback=arg_bytes20, metavar="0x...20", required=True, help="Pays for Gas to update IonLink")
@click.option('--lock', callback=arg_bytes20, metavar="0x...20", required=True, help="IonLock contract address")
@click.option('--link', callback=arg_bytes20, metavar="0x...20", required=True, help="IonLink contract address")
@click.option('--batch-size', type=int, default=32, metavar="N", help="Upload at most N items per transaction")
def etheventrelay(rpc_from, rpc_to, from_account, to_account, lock, link, batch_size):
    ionlock = rpc_from.proxy("abi/IonLock.abi", lock, from_account)
    batch = []
    transfers = []
    prev_root = merkle_hash("merkle-tree-extra")

    print("Starting block iterator")
    print("Latest Block: ", ionlock.LatestBlock())

    for is_latest, block_group in iter_blocks(rpc_from, ionlock.LatestBlock()):
        items, group_tx_count, group_log_count, transfers = lithium_process_block_group(rpc_from, block_group)
        print(len(items), len(transfers))
        if len(items):
            pack_items(items)
            print("blocks %d-%d (%d tx, %d events)" % (min(block_group), max(block_group), group_tx_count, group_log_count))
            item_tree, root = merkle_tree(items)
            batch.append( (block_group[0], root, transfers[0]) )

            # XXX This means the only time we get the proof is when the transfers[0] is true
            if transfers[0]==True:
                print("Items: ")
                for item in items:
                    print(item.encode('hex'))

                processProof(items[0], item_tree, rpc_from)

            # if len(batch) >= 2:
            if is_latest or len(batch) >= batch_size:
                print("Submitting batch of", len(batch), "blocks")
                prev_root = lithium_submit(batch, prev_root, rpc_to, link, to_account)
                batch = []
    return 0


if __name__ == "__main__":
    import sys
    from os import path
    sys.path.append(path.dirname(path.dirname(path.abspath(__file__))))
    etheventrelay()
