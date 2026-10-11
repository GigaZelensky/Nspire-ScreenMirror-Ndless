#undef NDEBUG
#include "wire.h"
#include "decoder.h"
#include "decoder_guard.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
static MirrorQueue q;
static uint16_t pixels[320*240];
void *sram_alloc(size_t bytes,size_t alignment) {(void)bytes;(void)alignment;return NULL;}
static void failures(void) {
    mirror_queue_init(&q);MirrorPacket hello={.session=73,.kind=MIRROR_HELLO};
    assert(mirror_accept(&q,&hello)==MIRROR_OK&&q.need_key);
    uint8_t payload[3]={1,2,3};
    MirrorPacket p={73,1,3,0,3,MIRROR_FRAME_DATA,MIRROR_FIRST|MIRROR_LAST,payload};
    assert(mirror_accept(&q,&p)==MIRROR_RESYNC);p.flags|=MIRROR_KEY;
    assert(mirror_accept(&q,&p)==MIRROR_OK&&mirror_credit(&q)==1);
    p.frame=2;assert(mirror_accept(&q,&p)==MIRROR_OK&&mirror_credit(&q)==0);
    p.frame=3;assert(mirror_accept(&q,&p)==MIRROR_BUSY);
    int active=mirror_take(&q);assert(active>=0&&q.frame[active].id==1&&mirror_credit(&q)==0);
    assert(mirror_accept(&q,&hello)==MIRROR_BUSY);
    q.frame[active].busy=false;assert(mirror_credit(&q)==1);
    active=mirror_take(&q);assert(active>=0&&q.frame[active].id==2);q.frame[active].busy=false;
    assert(mirror_accept(&q,&hello)==MIRROR_OK);
    p.frame=1;p.total=6;p.bytes=3;p.flags=MIRROR_FIRST|MIRROR_KEY;
    assert(mirror_accept(&q,&p)==MIRROR_OK);p.offset=4;p.flags=MIRROR_LAST|MIRROR_KEY;
    assert(mirror_accept(&q,&p)==MIRROR_BAD&&q.need_key&&q.assembling==-1);
    uint8_t data[32];mirror_reply(data,&q,0,1440);MirrorPacket parsed;
    assert(!mirror_parse(data,32,1440,&parsed));
    memset(data,0,32);memcpy(data,"NDM1",4);data[4]=1;data[5]=MIRROR_HELLO;wire_put(data+8,73);
    assert(mirror_parse(data,32,1440,&parsed));
    data[28]=1;assert(!mirror_parse(data,32,1440,&parsed));data[28]=0;
    data[6]=128;assert(!mirror_parse(data,32,1440,&parsed));data[6]=0;
    data[24]=1;assert(!mirror_parse(data,32,1440,&parsed));data[24]=0;
    for(unsigned n=0;n<32;++n)assert(!mirror_parse(data,n,1440,&parsed));
}
int main(int argc,char **argv) {
    assert(argc==3);failures();mirror_queue_init(&q);
    FILE *input=fopen(argv[1],"rb"),*out=fopen(argv[2],"wb");assert(input&&out);
    MirrorDecoder decoder={0};assert(mirror_decoder_init(&decoder));
    unsigned frames=0;
    for(;;) {
        uint8_t length[4],packet[MIRROR_PACKET];
        if(fread(length,1,4,input)==0)break;
        unsigned size=wire_get(length);assert(size<=sizeof(packet)&&fread(packet,1,size,input)==size);
        MirrorPacket parsed;assert(mirror_parse(packet,size,MIRROR_PACKET,&parsed));
        assert(mirror_accept(&q,&parsed)==MIRROR_OK);
        if(parsed.kind==MIRROR_HELLO)q.reset=false;
        int active=mirror_take(&q);
        if(active>=0) {
            MirrorFrame *f=&q.frame[active];int result=0;
            for(unsigned steps=0;steps<1000&&!result;++steps) {
                result=mirror_decoder_step(&decoder,f->data,f->total,f->id,pixels,320);
#if MIRROR_DECODE_GUARD
                if(!result) {
                    MirrorDecodeGuard guard={0};
                    assert(mirror_guard_begin(&guard,&decoder,f));
                    assert(mirror_guard_end(&guard)==0);
                }
#endif
            }
            if(result!=1){fprintf(stderr,"Decode failed: frame=%u result=%d offset=%u total=%u reason=%s\n",f->id,result,decoder.offset,f->total,decoder.storage->errorReason);return 1;}
            assert(fwrite(pixels,2,320*240,out)==320*240);q.displayed=f->id;f->busy=false;++frames;
        }
    }
    mirror_decoder_free(&decoder);fclose(input);fclose(out);
    printf("PASS actual C queue/parser fault cases and %u frames through production H264 decoder\n",frames);
    return 0;
}
