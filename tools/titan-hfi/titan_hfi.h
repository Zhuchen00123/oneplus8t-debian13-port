/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Titan (SM8250 / Kona CAMSS ICP + BPS + IPE) host<->firmware HFI wire contract.
 *
 * Provenance (offline only, no device touched):
 *   firmware   local/camera-r4-20261110/analysis/CAMERA_ICP.elf
 *              3,922,232 B, SHA-256 cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9
 *   version    CICP.FW.1.0.82-4-ICA_REG / AAAAAA / HARV-PSOMANCH (titan.icp.1.0.release82)
 *   extraction docs/camera-titan-hfi-b0-20261111.md (DWARF + opcode jump table)
 *   every size below is pinned by _Static_assert; every opcode size is
 *   re-derived from the firmware by tools/titan-hfi/extract_opcodes.py.
 *
 * Hardware-side facts encoded here (all measured, not inferred):
 *   - queue table = 24 B header + n * 956 B queue header, hardcoded 0x3bc stride;
 *     indices and q_size are 4-byte words; the whole table must live inside one
 *     4 KiB mapping (24 + 3*956 = 2892 <= 4096).
 *   - all packets start with { u32 nSize; u32 ePacketType; }.
 *   - domain = (ePacketType >> 24) & 7.
 *   - receive limit is 6019 B (0x1783) per packet; nSize must be 4-byte aligned
 *     and non-zero.
 *   - every HFI address field is a 32-bit SMMU address (not a 64-bit IOVA).
 *
 * THE ONE PLACE THE UPSTREAM RFC HEADER IS WRONG:
 *   struct hfi_cmd_async in camss-icp-hfi.h inserts a fw_handle at +12, which
 *   pushes num_handles to +32, handles to +36 and the payload to +40.
 *   The shipped firmware puts nUserArg1 at +12, nNumHandles at +28,
 *   hHandles at +32 and the payload at 32 + 4*nNumHandles.
 *   Copying upstream therefore mis-parses the packet. Follow this header.
 *   (The async ACK packet, in contrast, is byte-identical to upstream.)
 *
 * No register offsets, no firmware-loading interface and no IQ parameter
 * semantics are defined here: those are still unknown (see B0 doc section 9).
 */

#ifndef TITAN_HFI_H
#define TITAN_HFI_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------------- */
/* 0. base types                                                             */
/* ------------------------------------------------------------------------- */

/* Firmware typedefs, reproduced verbatim (firmware is ARM32, so every pointer
 * that crosses the wire is 32-bit).
 */
typedef uint64_t FWUSERARG;   /* cookie returned verbatim by the firmware */
typedef uint32_t SMMU_ADDR;   /* 32-bit SMMU/IOVA address, never 64-bit */
typedef uint32_t FWSTATUS;

#define TITAN_HFI_PACKED __attribute__((packed))
#define TITAN_HFI_UNUSED __attribute__((unused))

#if defined(__cplusplus) && __cplusplus >= 201103L
#define TITAN_HFI_SA(cond, msg) static_assert((cond), msg)
#else
#define TITAN_HFI_SA(cond, msg) _Static_assert((cond), msg)
#endif
#define TITAN_HFI_ASSERT(cond) TITAN_HFI_SA((cond), #cond)
#define TITAN_HFI_ASSERT_SIZE(type, size) \
	TITAN_HFI_SA(sizeof(struct type) == (size), "wrong size: " #type)
#define TITAN_HFI_ASSERT_OFF(type, member, off) \
	TITAN_HFI_SA(offsetof(struct type, member) == (off), \
		     "wrong offset: " #type "." #member)

/* ------------------------------------------------------------------------- */
/* 1. queue layer                                                            */
/* ------------------------------------------------------------------------- */

#define TITAN_HFI_QTBL_HDR_SIZE   24u
#define TITAN_HFI_Q_HDR_SIZE      956u   /* firmware hardcodes 0x3bc */
#define TITAN_HFI_QTBL_QHDR0_OFF  24u
#define TITAN_HFI_Q_MAX           3u
#define TITAN_HFI_QTBL_MAP_WINDOW 4096u  /* tryMemMapping(qtbl, 0x1000) */
#define TITAN_HFI_QTBL_VERSION    0xFFFFFFFFu /* written, never checked by fw */

/* queue ids, matched byte-wise against qhdr_type */
#define TITAN_HFI_Q_CMD 0u
#define TITAN_HFI_Q_MSG 1u
#define TITAN_HFI_Q_DBG 2u

struct titan_hfi_q_tbl_hdr {
	uint32_t version;          /* +0  not validated by the firmware */
	uint32_t size;             /* +4  total table bytes */
	uint32_t q_hdr0_offset;    /* +8  offset of the first queue header */
	uint32_t q_hdr_size;       /* +12 ignored: fw uses the constant 956 */
	uint32_t num_queues;       /* +16 */
	uint32_t num_active_queues;/* +20 */
} TITAN_HFI_PACKED;

/* 956 B = 14 fields, each preceded by 15 words of padding (firmware layout).
 * Indices, watermarks and q_size are counts of 4-byte words, not bytes:
 *   addr = start_addr + idx * 4
 */
struct titan_hfi_q_hdr {
	uint32_t pad0[15];
	uint32_t status;           /* +60 */
	uint32_t pad1[15];
	uint32_t start_addr;       /* +124 queue buffer byte address */
	uint32_t pad2[15];
	uint32_t type;             /* +188 0=cmd 1=msg 2=dbg */
	uint32_t pad3[15];
	uint32_t q_size;           /* +252 capacity in words */
	uint32_t pad4[15];
	uint32_t pkt_size;         /* +316 */
	uint32_t pad5[15];
	uint32_t pkt_drop_cnt;     /* +380 */
	uint32_t pad6[15];
	uint32_t rx_wm;            /* +444 */
	uint32_t pad7[15];
	uint32_t tx_wm;            /* +508 */
	uint32_t pad8[15];
	uint32_t rx_req;           /* +572 */
	uint32_t pad9[15];
	uint32_t tx_req;           /* +636 */
	uint32_t pad10[15];
	uint32_t rx_irq_status;    /* +700 */
	uint32_t pad11[15];
	uint32_t tx_irq_status;    /* +764 */
	uint32_t pad12[15];
	uint32_t read_idx;         /* +828 in words */
	uint32_t pad13[15];
	uint32_t write_idx;        /* +892 in words */
	uint32_t pad14[15];
} TITAN_HFI_PACKED;

/* ------------------------------------------------------------------------- */
/* 2. packet types                                                           */
/* ------------------------------------------------------------------------- */

/* domain nibble: enum TitanDomain { ICP = 0, IPE_BPS = 1, CDM = 2, DBG = 3 } */
#define TITAN_HFI_DOMAIN(pkt_type) (((pkt_type) >> 24) & 0x7u)
#define TITAN_HFI_DOMAIN_ICP     0u
#define TITAN_HFI_DOMAIN_IPE_BPS 1u
#define TITAN_HFI_DOMAIN_CDM     2u
#define TITAN_HFI_DOMAIN_DBG     3u

/* host -> ICP, system */
#define HFI_CMD_SYS_INIT         0x00010001u
#define HFI_CMD_SYS_PC_PREP      0x00010002u
#define HFI_CMD_SYS_SET_PROPERTY 0x00010003u
#define HFI_CMD_SYS_GET_PROPERTY 0x00010004u /* NOT implemented by the firmware */
#define HFI_CMD_SYS_PING         0x00010005u
#define HFI_CMD_SYS_RESET        0x00010006u

/* ICP -> host, system */
#define HFI_MSG_SYS_INIT_DONE     0x00020001u
#define HFI_MSG_SYS_PC_PREP_DONE  0x00020002u
#define HFI_MSG_SYS_DEBUG         0x00020003u
#define HFI_MSG_SYS_IDLE          0x00020004u /* defined upstream, unproven here */
#define HFI_MSG_SYS_PROPERTY_INFO 0x00020005u /* defined upstream, unproven here */
#define HFI_MSG_SYS_PING_ACK      0x00020006u
#define HFI_MSG_SYS_RESET_ACK     0x00020007u
#define HFI_MSG_EVENT_NOTIFY      0x00020008u

/* host -> ICP, IPE/BPS domain */
#define HFI_CMD_IPEBPS_CREATE_HANDLE        0x01010008u
#define HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT   0x0101000Au
#define HFI_CMD_IPEBPS_ASYNC_COMMAND_INDIRECT 0x0101000Eu

/* ICP -> host, IPE/BPS domain */
#define HFI_MSG_IPEBPS_CREATE_HANDLE_ACK    0x01020008u
#define HFI_MSG_IPEBPS_ASYNC_DIRECT_ACK     0x0102000Au
#define HFI_MSG_IPEBPS_ASYNC_INDIRECT_ACK   0x0102000Eu

/* debug domain (no upstream equivalent) */
#define HFI_CMD_DBG_TRACE_BUFFER 0x03010008u
#define HFI_CMD_DBG_REG_DUMP     0x03010009u

/* handle types accepted by HFI_CMD_IPEBPS_CREATE_HANDLE.
 * Firmware rejects type > 6, then tests a bitmask:
 *   types 1,5,6 -> BPS stream path (mask 0x62)
 *   types 2,3,4 -> IPE stream path (mask 0x1c)
 * The symbolic meaning of each value is not proven; only the grouping is.
 */
#define TITAN_HFI_HANDLE_TYPE_MAX        6u
#define TITAN_HFI_HANDLE_MASK_BPS        0x62u
#define TITAN_HFI_HANDLE_MASK_IPE        0x1Cu
#define TITAN_HFI_HANDLE_TYPE_BPS_1      1u
#define TITAN_HFI_HANDLE_TYPE_IPE_2      2u
#define TITAN_HFI_HANDLE_TYPE_IPE_3      3u
#define TITAN_HFI_HANDLE_TYPE_IPE_4      4u
#define TITAN_HFI_HANDLE_TYPE_BPS_5      5u
#define TITAN_HFI_HANDLE_TYPE_BPS_6      6u

/* opcodes, host -> ICP, carried in eCmdOpcode */
#define HFI_IPEBPS_CMD_OPCODE_BPS_CONFIG_IO        0x01u
#define HFI_IPEBPS_CMD_OPCODE_BPS_FRAME_PROCESS    0x02u
#define HFI_IPEBPS_CMD_OPCODE_BPS_ABORT            0x03u
#define HFI_IPEBPS_CMD_OPCODE_BPS_DESTROY          0x04u
#define HFI_IPEBPS_CMD_OPCODE_IPE_CONFIG_IO        0x05u
#define HFI_IPEBPS_CMD_OPCODE_IPE_FRAME_PROCESS    0x06u
#define HFI_IPEBPS_CMD_OPCODE_IPE_ABORT            0x07u
#define HFI_IPEBPS_CMD_OPCODE_IPE_DESTROY          0x08u
#define HFI_IPEBPS_CMD_OPCODE_BPS_WAIT_FOR_IPE     0x09u
#define HFI_IPEBPS_CMD_OPCODE_BPS_WAIT_FOR_BPS     0x0Au
#define HFI_IPEBPS_CMD_OPCODE_IPE_WAIT_FOR_BPS     0x0Bu
#define HFI_IPEBPS_CMD_OPCODE_IPE_WAIT_FOR_IPE     0x0Cu
#define HFI_IPEBPS_CMD_OPCODE_UNKNOWN_0D           0x0Du /* 16 B, no hardware, unknown */
#define HFI_IPEBPS_CMD_OPCODE_MEM_MAP              0x0Eu
#define HFI_IPEBPS_CMD_OPCODE_MEM_UNMAP            0x0Fu

/* ------------------------------------------------------------------------- */
/* 3. packet structures                                                      */
/* ------------------------------------------------------------------------- */

#define TITAN_HFI_PKT_HDR_SIZE   8u
#define TITAN_HFI_MAX_PKT_SIZE   6019u  /* 0x1783, ICPHostInterface_driverTask */

struct titan_hfi_pkt_hdr {
	uint32_t nSize;        /* +0  bytes, 4-byte aligned, non-zero */
	uint32_t ePacketType;  /* +4 */
} TITAN_HFI_PACKED;

struct titan_hfi_cmd_sys_init {          /* 8 B */
	struct titan_hfi_pkt_hdr hdr;
} TITAN_HFI_PACKED;

struct titan_hfi_cmd_ping {              /* 16 B, also ping/reset ACK */
	struct titan_hfi_pkt_hdr hdr;
	uint64_t nClientData;
} TITAN_HFI_PACKED;

struct titan_hfi_cmd_reset {             /* 16 B */
	struct titan_hfi_pkt_hdr hdr;
	uint64_t nClientData;
} TITAN_HFI_PACKED;

/* one property TLV: 12 B (firmware advances the cursor by 0xC per entry) */
struct titan_hfi_property {
	uint32_t id;         /* +0 */
	uint32_t value0;     /* +4 */
	uint32_t value1;     /* +8 */
} TITAN_HFI_PACKED;

/* Wire length is 12 B + 12 B per property (24 B for one property); the 16 B
 * below is the declared struct, whose rgPropertyData[1] is one u32 of payload.
 */
struct titan_hfi_cmd_sys_set_property {  /* 16 B declared, 12 + 12n on the wire */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t nNumProperties;
	uint32_t rgPropertyData[1];
} TITAN_HFI_PACKED;

struct titan_hfi_msg_sys_init_done {     /* 20 B declared, 16 + 12n on the wire */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t eErrorType;
	uint32_t nNumProperties;
	uint32_t rgPropertyData[1];
} TITAN_HFI_PACKED;

struct titan_hfi_msg_sys_pc_prep_done {  /* 12 B */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t eErrorType;
} TITAN_HFI_PACKED;

struct titan_hfi_msg_sys_debug {         /* 25 B + data */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t eMsgType;
	uint32_t nMsgSize;
	uint32_t nTimeStampHi;
	uint32_t nTimeStampLo;
	uint8_t rgMsgData[1];
} TITAN_HFI_PACKED;

struct titan_hfi_msg_event {             /* 28 B + ext data */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t nSessionID;
	uint32_t nEventID;
	uint32_t eventData1;
	uint32_t eventData2;
	uint32_t rgExtEventData[1];
} TITAN_HFI_PACKED;

struct titan_hfi_cmd_create_handle {     /* 28 B */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t eHandleType;
	uint64_t nUserArg1;
	uint64_t nUserArg2;
} TITAN_HFI_PACKED;

struct titan_hfi_msg_create_handle_ack { /* 32 B */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t eErrorType;
	uint32_t hHandle;
	uint64_t nUserArg1;
	uint64_t nUserArg2;
} TITAN_HFI_PACKED;

/*
 * Async command, firmware layout (fixed 40 B for one handle; payload follows).
 *
 *   +0  nSize
 *   +4  ePacketType     HFI_CMD_IPEBPS_ASYNC_COMMAND_{DIRECT,INDIRECT}
 *   +8  eCmdOpcode
 *   +12 nUserArg1 (u64) <- upstream has fw_handle here; do not follow upstream
 *   +20 nUserArg2 (u64)
 *   +28 nNumHandles
 *   +32 hHandles[nNumHandles]  (one or more)
 *   +32+4*nNumHandles  payload, either inline (DIRECT) or a u32 SMMU address
 *                      pointing at the payload (INDIRECT)
 *
 * The struct below is the nNumHandles == 1 specialisation exactly as the
 * firmware declares it (40 B). For n > 1 the handle array simply grows and
 * everything after it moves by 4 * (n - 1).
 */
struct titan_hfi_cmd_async {             /* 40 B, nNumHandles == 1 */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t eCmdOpcode;
	uint64_t nUserArg1;
	uint64_t nUserArg2;
	uint32_t nNumHandles;
	uint32_t hHandles[1];
	union {
		uint32_t direct[1];
		SMMU_ADDR indirect_addr;
	} payload;
} TITAN_HFI_PACKED;

struct titan_hfi_msg_async_ack {         /* 36 B + cmdPayload */
	struct titan_hfi_pkt_hdr hdr;
	uint32_t eCmdOpcode;
	uint64_t nUserArg1;
	uint64_t nUserArg2;
	uint32_t eErrorType;
	uint32_t cmdPayload[1];
} TITAN_HFI_PACKED;

/* ------------------------------------------------------------------------- */
/* 4. async payload placement (exactly CheckCommandSize() @0x22560)           */
/* ------------------------------------------------------------------------- */

#define TITAN_HFI_ASYNC_FIXED_HDR 32u  /* bytes before hHandles[] */
#define TITAN_HFI_ASYNC_MIN_SIZE  40u  /* indirect: 32 + 4 + 4 */

/* payload offset for a given handle count: 32 + 4*n */
#define TITAN_HFI_ASYNC_PAYLOAD_OFF(n) \
	(TITAN_HFI_ASYNC_FIXED_HDR + 4u * (uint32_t)(n))

/* DIRECT:   nSize >= payload_bytes + 32 + 4*n  (payload inline in the packet)
 * INDIRECT: nSize >= 40                        (packet holds a u32 SMMU address)
 */
static inline TITAN_HFI_UNUSED uint32_t
titan_hfi_async_direct_min_size(uint32_t payload_bytes,
						       uint32_t num_handles)
{
	return payload_bytes + TITAN_HFI_ASYNC_FIXED_HDR + 4u * num_handles;
}

static inline TITAN_HFI_UNUSED int titan_hfi_async_size_ok(uint32_t nSize,
					  uint32_t payload_bytes,
					  uint32_t num_handles, int direct)
{
	if (!nSize || (nSize & 3u) || nSize > TITAN_HFI_MAX_PKT_SIZE)
		return 0;
	if (direct)
		return nSize >= titan_hfi_async_direct_min_size(payload_bytes,
								 num_handles);
	return nSize >= TITAN_HFI_ASYNC_MIN_SIZE;
}

/* ------------------------------------------------------------------------- */
/* 5. payload ABI (no upstream equivalent; recovered from firmware DWARF)     */
/* ------------------------------------------------------------------------- */

/* enum ImageFormat (34 values, 4-byte) */
#define TITAN_HFI_IMAGE_FORMAT_INVALID            0u
#define TITAN_HFI_IMAGE_FORMAT_MIPI_8             1u
#define TITAN_HFI_IMAGE_FORMAT_MIPI_10            2u
#define TITAN_HFI_IMAGE_FORMAT_MIPI_12            3u
#define TITAN_HFI_IMAGE_FORMAT_MIPI_14            4u
#define TITAN_HFI_IMAGE_FORMAT_BAYER_8            5u
#define TITAN_HFI_IMAGE_FORMAT_BAYER_10           6u
#define TITAN_HFI_IMAGE_FORMAT_BAYER_12           7u
#define TITAN_HFI_IMAGE_FORMAT_BAYER_14           8u
#define TITAN_HFI_IMAGE_FORMAT_PDI_10             9u
#define TITAN_HFI_IMAGE_FORMAT_PD_10              10u
#define TITAN_HFI_IMAGE_FORMAT_LINEAR_PLAIN_8     11u
#define TITAN_HFI_IMAGE_FORMAT_INDICATIONS        12u
#define TITAN_HFI_IMAGE_FORMAT_REFINEMENT         13u
#define TITAN_HFI_IMAGE_FORMAT_UBWC_TP_10         14u
#define TITAN_HFI_IMAGE_FORMAT_UBWC_NV_12         15u
#define TITAN_HFI_IMAGE_FORMAT_UBWC_NV12_4R       16u
#define TITAN_HFI_IMAGE_FORMAT_UBWC_P010          17u
#define TITAN_HFI_IMAGE_FORMAT_LINEAR_TP_10       18u
#define TITAN_HFI_IMAGE_FORMAT_LINEAR_P010        19u
#define TITAN_HFI_IMAGE_FORMAT_LINEAR_NV12        20u
#define TITAN_HFI_IMAGE_FORMAT_LINEAR_PLAIN_16    21u
#define TITAN_HFI_IMAGE_FORMAT_YUV422_8           22u
#define TITAN_HFI_IMAGE_FORMAT_YUV422_10          23u
#define TITAN_HFI_IMAGE_FORMAT_STATS_BAYER_GRID   24u
#define TITAN_HFI_IMAGE_FORMAT_STATS_BAYER_HIST   25u
#define TITAN_HFI_IMAGE_FORMAT_ARGB_8_8_8_8       26u
#define TITAN_HFI_IMAGE_FORMAT_ARGB_2_10_10_10    27u
#define TITAN_HFI_IMAGE_FORMAT_ARGB_16_16_16_16   28u
#define TITAN_HFI_IMAGE_FORMAT_LMC                29u
#define TITAN_HFI_IMAGE_FORMAT_BAYER_16           30u
#define TITAN_HFI_IMAGE_FORMAT_BAYER_20           31u
#define TITAN_HFI_IMAGE_FORMAT_MIPI_20            32u
#define TITAN_HFI_IMAGE_FORMAT_STATS_HDR10PLUS    33u
#define TITAN_HFI_IMAGE_FORMAT_MAX                34u

/* enum BayerPixelOrder */
#define TITAN_HFI_FIRST_PIXEL_R   0u
#define TITAN_HFI_FIRST_PIXEL_GR  1u
#define TITAN_HFI_FIRST_PIXEL_B   2u
#define TITAN_HFI_FIRST_PIXEL_GB  3u

/* enum PixelPackingAlignment */
#define TITAN_HFI_PIXEL_LSB_ALIGNED 0u
#define TITAN_HFI_PIXEL_MSB_ALIGNED 1u

/* enum UbwcVersion / UbwcLossyMode */
#define TITAN_HFI_UBWC_VERSION_2 0u
#define TITAN_HFI_UBWC_VERSION_3 1u
#define TITAN_HFI_UBWC_VERSION_4 2u
#define TITAN_HFI_UBWC_LOSSLESS 0u
#define TITAN_HFI_UBWC_LOSSY    1u

/* enum IpeTopologyType */
#define TITAN_HFI_TOPOLOGY_TYPE_DEFAULT        0u
#define TITAN_HFI_TOPOLOGY_TYPE_MFSR_PREFILTER 1u
#define TITAN_HFI_TOPOLOGY_TYPE_MFSR_BLEND     2u
#define TITAN_HFI_TOPOLOGY_TYPE_NO_NPS_LTM     3u
#define TITAN_HFI_TOPOLOGY_TYPE_MFHDR          4u
#define TITAN_HFI_TOPOLOGY_TYPE_NPS_ONLY       5u
#define TITAN_HFI_TOPOLOGY_TYPE_MAX            6u

struct titan_hfi_image_dimensions {      /* 8 B */
	uint32_t widthPixels;
	uint32_t heightLines;
} TITAN_HFI_PACKED;

struct titan_hfi_buffer_layout {         /* 8 B */
	uint32_t bufferStride;
	uint32_t bufferHeight;
} TITAN_HFI_PACKED;

struct titan_hfi_image_info {            /* 60 B */
	uint32_t format;                  /* +0  ImageFormat */
	struct titan_hfi_image_dimensions dimensions; /* +4 */
	uint32_t bayerOrder;              /* +12 BayerPixelOrder */
	uint32_t pixelPackingAlignment;   /* +16 */
	uint32_t yuv422Order;             /* +20 Yuv422PixelOrder */
	uint32_t enableByteSwap;          /* +24 */
	uint32_t ubwcVersion;             /* +28 UbwcVersion */
	uint32_t ubwcLossyMode;           /* +32 UbwcLossyMode */
	uint32_t ubwcBwLimit;             /* +36 */
	uint32_t ubwcThreshLossy0;        /* +40 */
	uint32_t ubwcThreshLossy1;        /* +44 */
	uint32_t argbAlpha;               /* +48 */
	uint32_t ubwcOffsetsVarLossy;     /* +52 */
	uint32_t fromDownscale;           /* +56 */
} TITAN_HFI_PACKED;

struct titan_hfi_image_descriptor {      /* 92 B */
	struct titan_hfi_image_info info;                  /* +0  60 B */
	struct titan_hfi_buffer_layout bufferLayout[2];    /* +60 16 B */
	struct titan_hfi_buffer_layout metadataBufferLayout[2]; /* +76 16 B */
} TITAN_HFI_PACKED;

#define TITAN_HFI_BPS_MAX_IMAGES 9u
#define TITAN_HFI_IPE_MAX_IMAGES 17u

struct titan_hfi_bps_config_io_data {    /* 832 B */
	struct titan_hfi_image_descriptor images[TITAN_HFI_BPS_MAX_IMAGES];
	uint32_t secureMode;             /* +828 */
} TITAN_HFI_PACKED;

struct titan_hfi_bps_config_io {         /* 840 B  = opcode 0x1 payload */
	struct titan_hfi_bps_config_io_data cmdData;
	FWUSERARG userArg;               /* +832 */
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_config_io_data {    /* 1592 B */
	struct titan_hfi_image_descriptor images[TITAN_HFI_IPE_MAX_IMAGES];
	struct titan_hfi_image_dimensions stabilizationMargins; /* +1564 */
	uint32_t secureMode;             /* +1572 */
	uint32_t icaMaxUpscalingQ16;     /* +1576 */
	uint32_t maxBatchSize;           /* +1580 */
	uint32_t muxMode;                /* +1584 */
	uint32_t topologyType;           /* +1588 IpeTopologyType */
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_config_io {         /* 1600 B = opcode 0x5 payload */
	struct titan_hfi_ipe_config_io_data cmdData;
	FWUSERARG userArg;               /* +1592 */
} TITAN_HFI_PACKED;

/* accumulator region used by the IPE completion structures */
struct titan_hfi_scratch_intermediate {  /* 32 B */
	uint32_t offset[4];              /* +0 */
	uint32_t size[4];                /* +16 */
} TITAN_HFI_PACKED;

struct titan_hfi_mem_map_region_set {    /* 8 B */
	SMMU_ADDR swBufferBaseAddr;
	uint32_t swBufferSize;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_bps_mem_map {       /* 20 B = opcode 0xE / 0xF payload */
	FWUSERARG userArg;               /* +0 */
	uint32_t memMapRequestNum;       /* +8 */
	struct titan_hfi_mem_map_region_set memMapRegionSets[1]; /* +12 */
} TITAN_HFI_PACKED;

/* Abort payloads: the firmware declares uint32_t *requestIds; on ARM32 that
 * is a 4-byte field and on the wire it is an SMMU address.
 */
struct titan_hfi_bps_abort_data {        /* 8 B */
	uint32_t numRequestIds;
	SMMU_ADDR requestIds;
} TITAN_HFI_PACKED;

struct titan_hfi_bps_abort {             /* 16 B = opcode 0x3 payload */
	struct titan_hfi_bps_abort_data cmdData;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_abort_data {        /* 8 B */
	uint32_t numRequestIds;
	SMMU_ADDR requestIds;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_abort {             /* 16 B = opcode 0x7 payload */
	struct titan_hfi_ipe_abort_data cmdData;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_bps_destroy {           /* 8 B = opcode 0x4 payload */
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_destroy {           /* 8 B = opcode 0x8 payload */
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

/* wait-for payloads: 4-byte handle + 4 bytes implicit padding + u64 cookie */
struct titan_hfi_bps_wait_for_ipe {      /* 16 B = opcode 0x9 payload */
	uint32_t waitFor;                /* +0 handle */
	uint32_t reserved;               /* +4 implicit padding in the firmware */
	FWUSERARG userArg;               /* +8 */
} TITAN_HFI_PACKED;

struct titan_hfi_bps_wait_for_bps {      /* 16 B = opcode 0xA payload */
	uint32_t waitFor;
	uint32_t reserved;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_wait_for_bps {      /* 16 B = opcode 0xB payload */
	uint32_t waitFor;
	uint32_t reserved;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_wait_for_ipe {      /* 16 B = opcode 0xC payload */
	uint32_t waitFor;
	uint32_t reserved;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

/* Opaque IQ parameter blocks. Only the size is contractual here; the per-module
 * fields are the ISP tuning surface and are NOT recovered yet (B0 section 9).
 * They travel to the firmware as an SMMU address, never inline.
 */
struct titan_hfi_bps_iq_settings {       /* 1036 B */
	uint8_t opaque[1036];
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_iq_settings {       /* 3676 B */
	uint8_t opaque[3676];
} TITAN_HFI_PACKED;

/* ---- completion payloads: these are the ACK cmdPayload[] bodies ---- */

struct titan_hfi_bps_config_io_result {  /* 4 B */
	uint32_t cdmBufferSize;
} TITAN_HFI_PACKED;

struct titan_hfi_bps_config_io_done {    /* 16 B */
	FWSTATUS rc;                     /* +0 */
	struct titan_hfi_bps_config_io_result cmdRes; /* +4 */
	FWUSERARG userArg;               /* +8 */
} TITAN_HFI_PACKED;

struct titan_hfi_bps_frame_process_result { /* 8 B */
	uint32_t hangDumpBufferSize;
	SMMU_ADDR hangDumpBufferAddr;
} TITAN_HFI_PACKED;

struct titan_hfi_bps_frame_process_done { /* 24 B */
	FWSTATUS rc;                     /* +0 */
	struct titan_hfi_bps_frame_process_result cmdRes; /* +4 */
	uint32_t reserved;               /* +12 4 B alignment gap in the firmware */
	FWUSERARG userArg;               /* +16 */
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_config_io_result {  /* 136 B */
	uint32_t scratchMemSize;         /* +0 */
	uint32_t cdmBufferSize;          /* +4 */
	struct titan_hfi_scratch_intermediate scratchBufferPdi;  /* +8 */
	struct titan_hfi_scratch_intermediate scratchBufferTfi;  /* +40 */
	struct titan_hfi_scratch_intermediate scratchBufferTfr;  /* +72 */
	struct titan_hfi_scratch_intermediate scratchBufferLmci; /* +104 */
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_config_io_done {    /* 152 B */
	FWSTATUS rc;                     /* +0 */
	struct titan_hfi_ipe_config_io_result cmdRes; /* +4 */
	uint32_t reserved;               /* +140 4 B alignment gap in the firmware */
	FWUSERARG userArg;               /* +144 */
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_frame_process_result { /* 136 B */
	uint32_t hangDumpBufferSize;     /* +0 */
	SMMU_ADDR hangDumpBufferAddr;    /* +4 */
	struct titan_hfi_scratch_intermediate scratchBufferPdi;  /* +8 */
	struct titan_hfi_scratch_intermediate scratchBufferTfi;  /* +40 */
	struct titan_hfi_scratch_intermediate scratchBufferTfr;  /* +72 */
	struct titan_hfi_scratch_intermediate scratchBufferLmci; /* +104 */
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_frame_process_done { /* 152 B */
	FWSTATUS rc;                     /* +0 */
	struct titan_hfi_ipe_frame_process_result cmdRes; /* +4 */
	uint32_t reserved;               /* +140 4 B alignment gap in the firmware */
	FWUSERARG userArg;               /* +144 */
} TITAN_HFI_PACKED;

/* rc @0, cookie @8: 4 bytes of implicit padding in the firmware */
struct titan_hfi_bps_destroy_done {      /* 16 B */
	FWSTATUS rc;
	uint32_t reserved;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_destroy_done {      /* 16 B */
	FWSTATUS rc;
	uint32_t reserved;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_bps_abort_done {        /* 16 B */
	FWSTATUS rc;
	uint32_t reserved;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_abort_done {        /* 16 B */
	FWSTATUS rc;
	uint32_t reserved;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_handle_wait_done {      /* 16 B */
	FWSTATUS rc;
	uint32_t reserved;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

struct titan_hfi_ipe_bps_mem_map_done {  /* 12 B (u64 at +4, so packed matters) */
	FWSTATUS rc;
	FWUSERARG userArg;
} TITAN_HFI_PACKED;

/* ------------------------------------------------------------------------- */
/* 6. properties (HFI_CMD_SYS_SET_PROPERTY)                                  */
/* ------------------------------------------------------------------------- */

#define HFI_PROP_SYS_DEBUG_CFG      1u /* supported */
#define HFI_PROP_SYS_UBWC_CFG       2u /* supported, 8 B payload */
#define HFI_PROP_SYS_IMAGE_VER      3u /* NOT supported (read-only) */
#define HFI_PROP_SYS_SUPPORTED      4u /* NOT supported (read-only) */
#define HFI_PROP_SYS_IPEBPS_PC      5u /* supported */
#define HFI_PROP_SYS_INTERNAL_6     6u /* supported, upstream has no name */
#define HFI_PROP_SYS_INTERNAL_7     7u /* supported, upstream has no name */
#define HFI_PROP_SYS_HANG_DUMP_MODE 8u /* supported, upstream has no name */
#define HFI_PROP_SYS_UBWC_CFG_EX    9u /* supported, 16 B payload, upstream none */
#define HFI_PROP_SYS_MAX            9u

struct titan_hfi_ubwc_config {           /* 8 B, property 2 */
	uint32_t ubwcFetchModeCfg;
	uint32_t ubwcWriteModeCfg;
} TITAN_HFI_PACKED;

struct titan_hfi_ubwc_config_ex {        /* 16 B, property 9 */
	struct titan_hfi_ubwc_config bps;
	struct titan_hfi_ubwc_config ipe;
} TITAN_HFI_PACKED;

struct titan_hfi_property_hang_dump_mode { /* 4 B, property 8 */
	uint32_t hangDumpMode;
} TITAN_HFI_PACKED;

/* payload of the read-only property 4 (firmware type, 36 B) */
struct titan_hfi_system_supported {      /* 36 B */
	uint32_t apiVersion;             /* +0 */
	uint32_t deviceTitanVersion;     /* +4 Kona = 7 (TITAN_480_V1) */
	uint32_t numberICPHW;            /* +8 */
	uint32_t deviceHWVersion[4];     /* +12 */
	uint32_t bForce8BppIpe;          /* +28 */
	uint32_t reservedPlaceHolder;    /* +32 */
} TITAN_HFI_PACKED;

#define TITAN_HFI_TITAN_VERSION_KONA 7u

/* ------------------------------------------------------------------------- */
/* 7. opcode -> payload size table                                           */
/*                                                                           */
/* Single source of truth: the C header, the Python codec and              */
/* extract_opcodes.py all read this list. Each entry is                    */
/*   X(opcode, NAME, payload_bytes)                                         */
/* and payload_bytes is the argument passed to CheckCommandSize() by the    */
/* firmware, i.e. the size of the async payload, NOT the packet size.       */
/* ------------------------------------------------------------------------- */

#define TITAN_HFI_OPCODE_TABLE(X)                       \
	X(0x01, BPS_CONFIG_IO,      840)                \
	X(0x02, BPS_FRAME_PROCESS,  2360)               \
	X(0x03, BPS_ABORT,            16)               \
	X(0x04, BPS_DESTROY,           8)               \
	X(0x05, IPE_CONFIG_IO,      1600)               \
	X(0x06, IPE_FRAME_PROCESS, 14328)               \
	X(0x07, IPE_ABORT,            16)               \
	X(0x08, IPE_DESTROY,           8)               \
	X(0x09, BPS_WAIT_FOR_IPE,     16)               \
	X(0x0A, BPS_WAIT_FOR_BPS,     16)               \
	X(0x0B, IPE_WAIT_FOR_BPS,     16)               \
	X(0x0C, IPE_WAIT_FOR_IPE,     16)               \
	X(0x0D, UNKNOWN_0D,           16)               \
	X(0x0E, MEM_MAP,              20)               \
	X(0x0F, MEM_UNMAP,            20)

/* ------------------------------------------------------------------------- */
/* 8. compile-time contract                                                  */
/* ------------------------------------------------------------------------- */

TITAN_HFI_ASSERT_SIZE(titan_hfi_q_tbl_hdr, 24);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_tbl_hdr, version, 0);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_tbl_hdr, size, 4);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_tbl_hdr, q_hdr0_offset, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_tbl_hdr, q_hdr_size, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_tbl_hdr, num_queues, 16);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_tbl_hdr, num_active_queues, 20);

TITAN_HFI_ASSERT_SIZE(titan_hfi_q_hdr, 956);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, status, 60);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, start_addr, 124);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, type, 188);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, q_size, 252);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, pkt_size, 316);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, pkt_drop_cnt, 380);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, rx_wm, 444);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, tx_wm, 508);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, rx_req, 572);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, tx_req, 636);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, rx_irq_status, 700);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, tx_irq_status, 764);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, read_idx, 828);
TITAN_HFI_ASSERT_OFF(titan_hfi_q_hdr, write_idx, 892);

TITAN_HFI_ASSERT_SIZE(titan_hfi_pkt_hdr, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_pkt_hdr, nSize, 0);
TITAN_HFI_ASSERT_OFF(titan_hfi_pkt_hdr, ePacketType, 4);

TITAN_HFI_ASSERT_SIZE(titan_hfi_cmd_sys_init, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_cmd_ping, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_cmd_reset, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_property, 12);
TITAN_HFI_ASSERT_SIZE(titan_hfi_cmd_sys_set_property, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_msg_sys_init_done, 20);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_sys_init_done, eErrorType, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_sys_init_done, nNumProperties, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_sys_init_done, rgPropertyData, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_msg_sys_pc_prep_done, 12);
TITAN_HFI_ASSERT_SIZE(titan_hfi_msg_sys_debug, 25);
TITAN_HFI_ASSERT_SIZE(titan_hfi_msg_event, 28);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_event, nSessionID, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_event, nEventID, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_event, eventData1, 16);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_event, eventData2, 20);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_event, rgExtEventData, 24);

TITAN_HFI_ASSERT_SIZE(titan_hfi_cmd_create_handle, 28);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_create_handle, eHandleType, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_create_handle, nUserArg1, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_create_handle, nUserArg2, 20);
TITAN_HFI_ASSERT_SIZE(titan_hfi_msg_create_handle_ack, 32);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_create_handle_ack, eErrorType, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_create_handle_ack, hHandle, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_create_handle_ack, nUserArg1, 16);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_create_handle_ack, nUserArg2, 24);

TITAN_HFI_ASSERT_SIZE(titan_hfi_cmd_async, 40);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_async, eCmdOpcode, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_async, nUserArg1, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_async, nUserArg2, 20);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_async, nNumHandles, 28);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_async, hHandles, 32);
TITAN_HFI_ASSERT_OFF(titan_hfi_cmd_async, payload, 36);
TITAN_HFI_ASSERT(TITAN_HFI_ASYNC_PAYLOAD_OFF(1) == 36);
TITAN_HFI_ASSERT(TITAN_HFI_ASYNC_PAYLOAD_OFF(2) == 40);
TITAN_HFI_ASSERT(TITAN_HFI_ASYNC_PAYLOAD_OFF(3) == 44);
TITAN_HFI_ASSERT(TITAN_HFI_ASYNC_MIN_SIZE == 40);

TITAN_HFI_ASSERT_SIZE(titan_hfi_msg_async_ack, 36);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_async_ack, eCmdOpcode, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_async_ack, nUserArg1, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_async_ack, nUserArg2, 20);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_async_ack, eErrorType, 28);
TITAN_HFI_ASSERT_OFF(titan_hfi_msg_async_ack, cmdPayload, 32);

TITAN_HFI_ASSERT_SIZE(titan_hfi_image_dimensions, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_buffer_layout, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_image_info, 60);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, format, 0);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, dimensions, 4);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, bayerOrder, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, pixelPackingAlignment, 16);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, yuv422Order, 20);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, enableByteSwap, 24);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, ubwcVersion, 28);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, ubwcLossyMode, 32);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, ubwcBwLimit, 36);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, ubwcThreshLossy0, 40);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, ubwcThreshLossy1, 44);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, argbAlpha, 48);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, ubwcOffsetsVarLossy, 52);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_info, fromDownscale, 56);

TITAN_HFI_ASSERT_SIZE(titan_hfi_image_descriptor, 92);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_descriptor, info, 0);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_descriptor, bufferLayout, 60);
TITAN_HFI_ASSERT_OFF(titan_hfi_image_descriptor, metadataBufferLayout, 76);

TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_config_io_data, 832);
TITAN_HFI_ASSERT_OFF(titan_hfi_bps_config_io_data, secureMode, 828);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_config_io, 840);
TITAN_HFI_ASSERT_OFF(titan_hfi_bps_config_io, userArg, 832);

TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_config_io_data, 1592);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_data, stabilizationMargins, 1564);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_data, secureMode, 1572);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_data, icaMaxUpscalingQ16, 1576);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_data, maxBatchSize, 1580);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_data, muxMode, 1584);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_data, topologyType, 1588);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_config_io, 1600);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io, userArg, 1592);

TITAN_HFI_ASSERT_SIZE(titan_hfi_scratch_intermediate, 32);
TITAN_HFI_ASSERT_OFF(titan_hfi_scratch_intermediate, offset, 0);
TITAN_HFI_ASSERT_OFF(titan_hfi_scratch_intermediate, size, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_mem_map_region_set, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_bps_mem_map, 20);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_bps_mem_map, userArg, 0);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_bps_mem_map, memMapRequestNum, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_bps_mem_map, memMapRegionSets, 12);

TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_abort_data, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_abort, 16);
TITAN_HFI_ASSERT_OFF(titan_hfi_bps_abort, userArg, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_abort_data, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_abort, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_destroy, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_destroy, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_wait_for_ipe, 16);
TITAN_HFI_ASSERT_OFF(titan_hfi_bps_wait_for_ipe, userArg, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_wait_for_bps, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_wait_for_bps, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_wait_for_ipe, 16);

TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_iq_settings, 1036);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_iq_settings, 3676);

TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_config_io_result, 4);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_config_io_done, 16);
TITAN_HFI_ASSERT_OFF(titan_hfi_bps_config_io_done, userArg, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_frame_process_result, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_frame_process_done, 24);
TITAN_HFI_ASSERT_OFF(titan_hfi_bps_frame_process_done, userArg, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_config_io_result, 136);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_result, scratchBufferPdi, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_result, scratchBufferTfi, 40);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_result, scratchBufferTfr, 72);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_result, scratchBufferLmci, 104);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_config_io_done, 152);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_config_io_done, userArg, 144);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_frame_process_result, 136);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_frame_process_done, 152);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_frame_process_done, userArg, 144);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_destroy_done, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_destroy_done, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_bps_abort_done, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_abort_done, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_handle_wait_done, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ipe_bps_mem_map_done, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_ipe_bps_mem_map_done, userArg, 4);

TITAN_HFI_ASSERT_SIZE(titan_hfi_ubwc_config, 8);
TITAN_HFI_ASSERT_SIZE(titan_hfi_ubwc_config_ex, 16);
TITAN_HFI_ASSERT_SIZE(titan_hfi_property_hang_dump_mode, 4);
TITAN_HFI_ASSERT_SIZE(titan_hfi_system_supported, 36);
TITAN_HFI_ASSERT_OFF(titan_hfi_system_supported, apiVersion, 0);
TITAN_HFI_ASSERT_OFF(titan_hfi_system_supported, deviceTitanVersion, 4);
TITAN_HFI_ASSERT_OFF(titan_hfi_system_supported, numberICPHW, 8);
TITAN_HFI_ASSERT_OFF(titan_hfi_system_supported, deviceHWVersion, 12);
TITAN_HFI_ASSERT_OFF(titan_hfi_system_supported, bForce8BppIpe, 28);
TITAN_HFI_ASSERT_OFF(titan_hfi_system_supported, reservedPlaceHolder, 32);

/* the queue table of 3 queues must fit the firmware's single 4 KiB window */
TITAN_HFI_ASSERT(TITAN_HFI_QTBL_HDR_SIZE +
		 TITAN_HFI_Q_MAX * TITAN_HFI_Q_HDR_SIZE <=
		 TITAN_HFI_QTBL_MAP_WINDOW);

#ifdef __cplusplus
}
#endif

#endif /* TITAN_HFI_H */
