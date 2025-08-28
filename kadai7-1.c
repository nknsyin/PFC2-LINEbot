#include<stdio.h>
#include<stdlib.h>
#include<time.h>
int main(){
    int i;
    srand(time(NULL));
    rand()%3;
    if(i==0){
        printf("コンピュータはグーです。");
    }
    else if(i==1){
        printf("コンピュータはパーです。");
    }
    else(i==2){
        printf("コンピュータはチョキです。");
    }
}