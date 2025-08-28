#include<stdio.h>
#include<stdlib.h>
#include<time.h>
int main(){
    srand(time(NULL));
    int i,n;
    printf("手を入れてください。(グーは０，パーは1、チョキは２):");
    scanf("%d,&i");
    n=rand()%3;
    if(n==0){
        printf("コンピュータはグーです。\n");
    }else if(n==1){
        printf("コンピュータはパーです。\n");
    }else(n==2){
        printf("コンピュータはチョキです。\n");
    }
    if(i==n){
        printf("あいこになりました。\n");
    }else if((i==0&&n==2)||(i==1&&n==0)||(i==2&&n==1)){
        printf("あなたの勝ちです。\n");
    }else{
        printf("あなたの負けです。\n");
    }
}