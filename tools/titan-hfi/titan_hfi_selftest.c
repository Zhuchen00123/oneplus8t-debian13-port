/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Compile-and-run proof for tools/titan-hfi/titan_hfi.h.
 *
 * The header pins every wire size with _Static_assert, so a wrong number cannot
 * compile. This program then prints the same numbers and cross-checks the
 * opcode -> payload-size table against the payload struct sizes, so the build
 * log is usable as evidence in docs/camera-titan-hfi-b1-20261111.md.
 *
 * Build:  cc -std=c11 -Wall -Wextra -Werror -pedantic -o titan_hfi_selftest \
 *            titan_hfi_selftest.c
 */

#include <stdio.h>

#include "titan_hfi.h"

#define TITAN_HFI_OPCODE_TABLE_X(op, name, size) \
	{ (op), #name, (uint32_t)(size) },

struct opcode_entry {
	uint32_t opcode;
	const char *name;
	uint32_t payload_size;
};

static const struct opcode_entry opcode_table[] = {
	TITAN_HFI_OPCODE_TABLE(TITAN_HFI_OPCODE_TABLE_X)
};

/* opcode <-> payload struct binding; a size mismatch is a contract break */
struct struct_binding {
	uint32_t opcode;
	size_t struct_size;
	const char *struct_name;
};

#define TITAN_HFI_BIND(op, cname) \
	{ (op), sizeof(struct cname), #cname }

static const struct struct_binding struct_bindings[] = {
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_BPS_CONFIG_IO,
		       titan_hfi_bps_config_io),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_BPS_ABORT, titan_hfi_bps_abort),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_BPS_DESTROY, titan_hfi_bps_destroy),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_IPE_CONFIG_IO,
		       titan_hfi_ipe_config_io),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_IPE_ABORT, titan_hfi_ipe_abort),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_IPE_DESTROY, titan_hfi_ipe_destroy),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_BPS_WAIT_FOR_IPE,
		       titan_hfi_bps_wait_for_ipe),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_BPS_WAIT_FOR_BPS,
		       titan_hfi_bps_wait_for_bps),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_IPE_WAIT_FOR_BPS,
		       titan_hfi_ipe_wait_for_bps),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_IPE_WAIT_FOR_IPE,
		       titan_hfi_ipe_wait_for_ipe),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_MEM_MAP,
		       titan_hfi_ipe_bps_mem_map),
	TITAN_HFI_BIND(HFI_IPEBPS_CMD_OPCODE_MEM_UNMAP,
		       titan_hfi_ipe_bps_mem_map),
};

static int failures;

static void check(int cond, const char *what)
{
	if (!cond) {
		printf("  FAIL %s\n", what);
		failures++;
	}
}

static void row_size(const char *name, size_t size, size_t expected)
{
	printf("  %-40s %6zu B  expected %6zu  %s\n", name, size, expected,
	       size == expected ? "ok" : "MISMATCH");
	if (size != expected)
		failures++;
}

static void row_off(const char *name, size_t off, size_t expected)
{
	printf("  %-40s +%-5zu          expected +%-5zu  %s\n", name, off,
	       expected, off == expected ? "ok" : "MISMATCH");
	if (off != expected)
		failures++;
}

int main(void)
{
	size_t i;

	printf("titan-hfi contract selftest\n");
	printf("firmware CICP.FW.1.0.82-4-ICA_REG (Kona TITAN_480_V1)\n\n");

	printf("queue layer\n");
	row_size("struct titan_hfi_q_tbl_hdr", sizeof(struct titan_hfi_q_tbl_hdr), 24);
	row_size("struct titan_hfi_q_hdr", sizeof(struct titan_hfi_q_hdr), 956);
	row_off("qhdr.status", offsetof(struct titan_hfi_q_hdr, status), 60);
	row_off("qhdr.start_addr", offsetof(struct titan_hfi_q_hdr, start_addr), 124);
	row_off("qhdr.type", offsetof(struct titan_hfi_q_hdr, type), 188);
	row_off("qhdr.q_size", offsetof(struct titan_hfi_q_hdr, q_size), 252);
	row_off("qhdr.read_idx", offsetof(struct titan_hfi_q_hdr, read_idx), 828);
	row_off("qhdr.write_idx", offsetof(struct titan_hfi_q_hdr, write_idx), 892);
	printf("  queue table for 3 queues: %u B (limit %u B)\n",
	       TITAN_HFI_QTBL_HDR_SIZE + TITAN_HFI_Q_MAX * TITAN_HFI_Q_HDR_SIZE,
	       TITAN_HFI_QTBL_MAP_WINDOW);
	check(TITAN_HFI_QTBL_HDR_SIZE + TITAN_HFI_Q_MAX * TITAN_HFI_Q_HDR_SIZE <=
		      TITAN_HFI_QTBL_MAP_WINDOW,
	      "3-queue table fits one 4 KiB mapping");

	printf("\npackets\n");
	row_size("struct titan_hfi_pkt_hdr", sizeof(struct titan_hfi_pkt_hdr), 8);
	row_size("struct titan_hfi_cmd_sys_init", sizeof(struct titan_hfi_cmd_sys_init), 8);
	row_size("struct titan_hfi_cmd_ping", sizeof(struct titan_hfi_cmd_ping), 16);
	row_size("struct titan_hfi_cmd_reset", sizeof(struct titan_hfi_cmd_reset), 16);
	row_size("struct titan_hfi_property", sizeof(struct titan_hfi_property), 12);
	row_size("struct titan_hfi_cmd_sys_set_property",
		 sizeof(struct titan_hfi_cmd_sys_set_property), 16);
	row_size("struct titan_hfi_msg_sys_init_done",
		 sizeof(struct titan_hfi_msg_sys_init_done), 20);
	row_size("struct titan_hfi_msg_sys_pc_prep_done",
		 sizeof(struct titan_hfi_msg_sys_pc_prep_done), 12);
	row_size("struct titan_hfi_msg_event", sizeof(struct titan_hfi_msg_event), 28);
	row_size("struct titan_hfi_cmd_create_handle",
		 sizeof(struct titan_hfi_cmd_create_handle), 28);
	row_size("struct titan_hfi_msg_create_handle_ack",
		 sizeof(struct titan_hfi_msg_create_handle_ack), 32);
	row_size("struct titan_hfi_cmd_async", sizeof(struct titan_hfi_cmd_async), 40);
	row_off("async.nUserArg1", offsetof(struct titan_hfi_cmd_async, nUserArg1), 12);
	row_off("async.nNumHandles", offsetof(struct titan_hfi_cmd_async, nNumHandles), 28);
	row_off("async.hHandles", offsetof(struct titan_hfi_cmd_async, hHandles), 32);
	row_off("async.payload", offsetof(struct titan_hfi_cmd_async, payload), 36);
	row_size("struct titan_hfi_msg_async_ack",
		 sizeof(struct titan_hfi_msg_async_ack), 36);
	row_off("ack.cmdPayload",
		offsetof(struct titan_hfi_msg_async_ack, cmdPayload), 32);

	printf("\nasync payload placement (firmware CheckCommandSize)\n");
	for (i = 1; i <= 3; i++)
		printf("  n=%zu -> payload offset %u; direct packet for an 8 B payload = %u B\n",
		       i, TITAN_HFI_ASYNC_PAYLOAD_OFF(i),
		       titan_hfi_async_direct_min_size(8u, (uint32_t)i));
	check(TITAN_HFI_ASYNC_PAYLOAD_OFF(1) == 36, "payload offset n=1 is 36");
	check(TITAN_HFI_ASYNC_PAYLOAD_OFF(2) == 40, "payload offset n=2 is 40");
	check(TITAN_HFI_ASYNC_PAYLOAD_OFF(3) == 44, "payload offset n=3 is 44");
	check(titan_hfi_async_size_ok(840 + 32 + 4, 840, 1, 1),
	      "bad: direct 840 B with n=1 needs 876 B");
	check(!titan_hfi_async_size_ok(840 + 32 + 4 - 4, 840, 1, 1),
	      "direct packet 4 B short must be rejected");
	check(titan_hfi_async_size_ok(40, 14328, 1, 0),
	      "indirect 14328 B payload accepted at 40 B packet");
	check(!titan_hfi_async_size_ok(39, 14328, 1, 0),
	      "indirect packet of 39 B must be rejected");

	printf("\npayload ABI\n");
	row_size("struct titan_hfi_image_dimensions",
		 sizeof(struct titan_hfi_image_dimensions), 8);
	row_size("struct titan_hfi_buffer_layout",
		 sizeof(struct titan_hfi_buffer_layout), 8);
	row_size("struct titan_hfi_image_info", sizeof(struct titan_hfi_image_info), 60);
	row_size("struct titan_hfi_image_descriptor",
		 sizeof(struct titan_hfi_image_descriptor), 92);
	row_size("struct titan_hfi_bps_config_io_data",
		 sizeof(struct titan_hfi_bps_config_io_data), 832);
	row_size("struct titan_hfi_ipe_config_io_data",
		 sizeof(struct titan_hfi_ipe_config_io_data), 1592);
	row_size("struct titan_hfi_bps_iq_settings",
		 sizeof(struct titan_hfi_bps_iq_settings), 1036);
	row_size("struct titan_hfi_ipe_iq_settings",
		 sizeof(struct titan_hfi_ipe_iq_settings), 3676);
	row_size("struct titan_hfi_bps_config_io_done",
		 sizeof(struct titan_hfi_bps_config_io_done), 16);
	row_size("struct titan_hfi_bps_frame_process_done",
		 sizeof(struct titan_hfi_bps_frame_process_done), 24);
	row_size("struct titan_hfi_ipe_config_io_done",
		 sizeof(struct titan_hfi_ipe_config_io_done), 152);
	row_size("struct titan_hfi_ipe_frame_process_done",
		 sizeof(struct titan_hfi_ipe_frame_process_done), 152);
	row_size("struct titan_hfi_ipe_bps_mem_map_done",
		 sizeof(struct titan_hfi_ipe_bps_mem_map_done), 12);
	row_size("struct titan_hfi_system_supported",
		 sizeof(struct titan_hfi_system_supported), 36);

	printf("\nopcode -> payload size, cross-checked against struct sizes\n");
	for (i = 0; i < sizeof(opcode_table) / sizeof(opcode_table[0]); i++) {
		size_t j;
		int bound = 0;

		for (j = 0; j < sizeof(struct_bindings) / sizeof(struct_bindings[0]); j++) {
			if (struct_bindings[j].opcode != opcode_table[i].opcode)
				continue;
			bound = 1;
			printf("  0x%02x %-20s table %5u B  sizeof(%s) %5zu B  %s\n",
			       opcode_table[i].opcode, opcode_table[i].name,
			       opcode_table[i].payload_size,
			       struct_bindings[j].struct_name,
			       struct_bindings[j].struct_size,
			       struct_bindings[j].struct_size ==
					       opcode_table[i].payload_size ?
				       "ok" : "MISMATCH");
			if (struct_bindings[j].struct_size !=
			    opcode_table[i].payload_size)
				failures++;
		}
		if (!bound)
			printf("  0x%02x %-20s table %5u B  (no C struct: opaque)\n",
			       opcode_table[i].opcode, opcode_table[i].name,
			       opcode_table[i].payload_size);
	}

	printf("\n%s (%d failure%s)\n", failures ? "FAILED" : "PASS",
	       failures, failures == 1 ? "" : "s");
	return failures ? 1 : 0;
}
