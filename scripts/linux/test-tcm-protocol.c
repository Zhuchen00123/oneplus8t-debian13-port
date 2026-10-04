/* SPDX-License-Identifier: GPL-2.0-only */
/* Build against the actual kernel patch header, not a copied decoder:
 * gcc -Wall -Wextra -Werror -fsanitize=address,undefined -g
 *   -I "$KERNEL_DIR/drivers/input/touchscreen" test-tcm-protocol.c -o test-tcm
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "synaptics_tcm_report.h"

static const u8 kb2000[] = {
  16,8,198,48,1,6,4,7,4,8,12,9,12,11,12,12,12,3,0
};
static unsigned int rng = 0x82503908;
static unsigned int random_u32(void)
{
  rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
  return rng;
}
static void put_bits(u8 *p, unsigned int *offset, unsigned int v, unsigned int n)
{
  unsigned int i;
  assert(*offset + n <= 252 * 8);
  for (i = 0; i < n; i++, (*offset)++)
    if ((v >> i) & 1) p[*offset / 8] |= 1U << (*offset & 7);
}
static void object(u8 *p, unsigned int *off, unsigned int index,
                   unsigned int cl, unsigned int x, unsigned int y,
                   unsigned int wx, unsigned int wy)
{
  put_bits(p,off,index,4); put_bits(p,off,cl,4);
  put_bits(p,off,x,12); put_bits(p,off,y,12);
  put_bits(p,off,wx,12); put_bits(p,off,wy,12);
}
static void bad_frame(const struct tcm_layout *l, const u8 *p, unsigned int n)
{
  struct tcm_frame f, before;
  memset(&f,0xa5,sizeof(f)); before = f;
  assert(tcm_parse_frame(l,p,n,10,&f) < 0);
  assert(!memcmp(&f,&before,sizeof(f)));
}
static void bad_config(const u8 *p, unsigned int n)
{
  struct tcm_layout l, before;
  memset(&l,0xa5,sizeof(l)); before = l;
  assert(tcm_compile_layout(p,n,&l) < 0);
  assert(!memcmp(&l,&before,sizeof(l)));
}
static void packet_tests(void)
{
  u8 b[256] = {0xa5,1,0,0,0x5a};
  struct tcm_packet p, before;
  unsigned int i;
  assert(tcm_decode_packet(b,sizeof(b),&p)==1 && !p.payload_size);
  for(i=0;i<5;i++) assert(tcm_decode_packet(b,i,&p)<0);
  for(i=12;i<=15;i++) {b[1]=i;assert(tcm_decode_packet(b,sizeof(b),&p)==1);}
  for(i=4;i<12;i++) {b[1]=i;assert(tcm_decode_packet(b,sizeof(b),&p)<0);}
  b[1]=0x11;b[2]=14;b[18]=0x5a;
  assert(tcm_decode_packet(b,sizeof(b),&p)==2 && p.payload_size==14);
  b[2]=21;b[25]=0x5a;
  assert(tcm_decode_packet(b,sizeof(b),&p)==2 && p.payload_size==21);
  b[1]=0x10;assert(tcm_decode_packet(b,sizeof(b),&p)==0);
  b[1]=0x11;b[2]=251;b[255]=0x5a;
  assert(tcm_decode_packet(b,sizeof(b),&p)==2 && p.payload_size==251);
  b[2]=252;assert(tcm_decode_packet(b,sizeof(b),&p)==-EMSGSIZE);
  b[2]=0;b[3]=1;assert(tcm_decode_packet(b,sizeof(b),&p)==-EMSGSIZE);
  b[1]=1;b[2]=b[3]=0;b[4]=0;
  memset(&p,0xa5,sizeof(p));before=p;
  assert(tcm_decode_packet(b,sizeof(b),&p)==-EPROTO && !memcmp(&p,&before,sizeof(p)));
  b[2]=b[3]=0xff;
  for(i=0;i<4;i++) if(i!=1) {
    b[1]=i;assert(tcm_decode_packet(b,sizeof(b),&p)==0);
    assert(!memcmp(&p,&before,sizeof(p)));
  }
  b[0]=0;assert(tcm_decode_packet(b,sizeof(b),&p)==-EPROTO);
}
int main(void)
{
  packet_tests();
  struct tcm_layout l, c;
  struct tcm_frame f;
  u8 p[252], cfg[252];
  unsigned int n, i, off;
  assert(!tcm_compile_layout(kb2000,sizeof(kb2000),&l));
  assert(l.object_bits == 56 && !l.counted && !l.pressure_bits && l.width_bits == 12);
  for (n = 0; n <= 10; n++) {
    memset(p,0,sizeof(p)); off = 56; /* seven-byte gesture prefix */
    for (i = 0; i < n; i++) object(p,&off,i,1,100+i,200+i,16+i,20+i);
    assert(off/8 == 7+7*n);
    assert(!tcm_parse_frame(&l,p,off/8,10,&f) && f.count == n);
    for (i = 0; i < n; i++) {
      assert(f.objects[i].index == i && f.objects[i].classification == 1);
      assert(f.objects[i].x == 100+i && f.objects[i].y == 200+i);
      assert(f.objects[i].wx == 16+i && f.objects[i].wy == 20+i);
    }
    for (i = 0; i < off/8; i++)
      if (i < 7 || (i-7)%7) bad_frame(&l,p,i);
  }
  memset(p,0,sizeof(p)); off=56;
  object(p,&off,9,2,1079,2399,4095,0); /* cross-byte bounds and glove */
  assert(!tcm_parse_frame(&l,p,14,10,&f));
  assert(f.objects[0].x == 1079 && f.objects[0].y == 2399 && f.objects[0].wx == 4095);
  memset(p,0,sizeof(p)); off=56; object(p,&off,10,1,0,0,0,0); bad_frame(&l,p,14);
  memset(p,0,sizeof(p)); off=56;
  object(p,&off,2,1,0,0,0,0); object(p,&off,2,1,0,0,0,0); bad_frame(&l,p,21);
  memset(p,0,sizeof(p)); off=56;
  for(i=0;i<11;i++) object(p,&off,i%10,1,0,0,0,0);
  bad_frame(&l,p,84);
  memset(p,0,sizeof(p)); off=56; object(p,&off,0,0,0,0,0,0);
  assert(!tcm_parse_frame(&l,p,14,10,&f) && f.objects[0].classification == 0);
  /* Byte alignment opcode has no width byte. Pressure is optional. */
  { const u8 aligned[]={24,4,4,1,6,4,7,4,8,12,9,12,10,8,4,3,0};
    assert(!tcm_compile_layout(aligned,sizeof(aligned),&c));
    memset(p,0,sizeof(p)); off=0; put_bits(p,&off,1,4); off=8;
    put_bits(p,&off,3,4); put_bits(p,&off,1,4);
    put_bits(p,&off,1000,12); put_bits(p,&off,2000,12); put_bits(p,&off,255,8);
    assert(!tcm_parse_frame(&c,p,6,10,&f));
    assert(f.count==1 && f.objects[0].index==3 && f.objects[0].z==255);
    memset(p,0,sizeof(p)); assert(!tcm_parse_frame(&c,p,1,10,&f) && !f.count);
    p[0]=11; bad_frame(&c,p,1);
  }
  /* Counted loops can have suffix scalar fields. All-object loops are fixed. */
  { const u8 counted[]={24,8,1,6,4,7,4,8,12,9,12,3,25,8,0};
    assert(!tcm_compile_layout(counted,sizeof(counted),&c));
    memset(p,0,sizeof(p)); p[0]=0; p[1]=99;
    assert(!tcm_parse_frame(&c,p,2,10,&f) && !f.count);
    bad_frame(&c,p,1); bad_frame(&c,p,3);
    const u8 all[]={2,6,4,7,4,8,12,9,12,3,0};
    assert(!tcm_compile_layout(all,sizeof(all),&c));
    memset(p,0,sizeof(p)); off=0;
    for(i=0;i<10;i++) {put_bits(p,&off,i,4);put_bits(p,&off,0,4);put_bits(p,&off,0,24);}
    assert(!tcm_parse_frame(&c,p,40,10,&f) && f.count==10);
    bad_frame(&c,p,39);
  }
  memset(cfg,0,sizeof(cfg)); memcpy(cfg,kb2000,sizeof(kb2000));
  assert(!tcm_compile_layout(cfg,128,&c));
  cfg[100]=1; bad_config(cfg,128);
  for(i=0;i<sizeof(kb2000);i++) bad_config(kb2000,i);
  memcpy(cfg,kb2000,sizeof(kb2000)); cfg[9]=17; bad_config(cfg,sizeof(kb2000));
  cfg[9]=0; bad_config(cfg,sizeof(kb2000));
  { const u8 nested[]={1,2,0}; bad_config(nested,sizeof(nested)); }
  { const u8 no_index[]={1,7,4,8,12,9,12,3,0}; bad_config(no_index,sizeof(no_index)); }
  /* Deterministic random valid frames, then raw config/payload mutation. */
  for(n=0;n<20000;n++) {
    unsigned int count=random_u32()%11;
    memset(p,0,sizeof(p)); off=56;
    for(i=0;i<count;i++) object(p,&off,i,random_u32()%3,random_u32()%1080,
                               random_u32()%2400,random_u32()%4096,random_u32()%4096);
    assert(!tcm_parse_frame(&l,p,off/8,10,&f) && f.count==count);
  }
  for(n=0;n<100000;n++) {
    unsigned int cs=random_u32()%253, ps=random_u32()%253;
    for(i=0;i<252;i++) {cfg[i]=random_u32();p[i]=random_u32();}
    if(n%2) {memset(cfg,0,sizeof(cfg));memcpy(cfg,kb2000,sizeof(kb2000));cfg[random_u32()%128]=random_u32();cs=128;}
    if(!tcm_compile_layout(cfg,cs,&c)) {
      struct tcm_frame before;
      memset(&f,0xa5,sizeof(f));before=f;
      if(tcm_parse_frame(&c,p,ps,10,&f)) assert(!memcmp(&f,&before,sizeof(f)));
      else assert(f.count<=10);
    }
    (void)tcm_parse_frame(&l,p,ps,10,&f);
  }
  puts("PASS: KB2000 0..10 contacts, 14/21-byte reports, lift, PAD, counted/all loops, bounds, atomic rejection, 120000 randomized cases");
  return 0;
}
